"""模拟组合表现分析(新增,不动交易逻辑):
- compute_attribution: 组合级收益归因 —— 每只持过的股(现持仓未实现 + 已平仓已实现)
  对初始资金的贡献(百分点),排出功臣/拖累。
- compute_position_perf: 单票持有期分析 —— adj_close 归一曲线 + 关键点(持有天数/峰谷/回撤)
  + vs 沪深300 / vs 等权自选池 的超额。
口径复用 portfolio_analytics._journal_stats 的 FIFO(买入含佣、卖出扣佣+印花)。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from config import DB_PATH


def _conn(conn: sqlite3.Connection | None) -> tuple[sqlite3.Connection, bool]:
    if conn is not None:
        return conn, False
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c, True


def _journal(conn: sqlite3.Connection, portfolio_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT trade_date, code, name, action, shares, price
           FROM trade_journal WHERE portfolio_id=? ORDER BY trade_date, id""",
        (portfolio_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _per_stock_realized(journal: list[dict]) -> dict[str, dict]:
    """FIFO 按股统计已实现盈亏(含佣/印花),口径与 avg_cost 一致。"""
    from services.trading_rules import COMMISSION, STAMP_TAX

    lots: dict[str, list[dict]] = {}
    out: dict[str, dict] = {}
    for j in journal:  # 已按 trade_date 排序
        code = j.get("code", "")
        action = (j.get("action") or "").upper()
        sh = int(j.get("shares") or 0)
        price = float(j.get("price") or 0)
        rec = out.setdefault(code, {"realized": 0.0, "closed_qty": 0, "name": j.get("name") or code})
        if action == "BUY":
            lots.setdefault(code, []).append({"shares": sh, "price": price * (1 + COMMISSION)})
        elif action == "SELL" and lots.get(code):
            net = price * (1 - COMMISSION - STAMP_TAX)
            rem = sh
            while rem > 0 and lots[code]:
                lot = lots[code][0]
                take = min(rem, lot["shares"])
                rec["realized"] += take * (net - lot["price"])
                rec["closed_qty"] += take
                lot["shares"] -= take
                rem -= take
                if lot["shares"] <= 0:
                    lots[code].pop(0)
    return out


def _latest_adj_close(conn: sqlite3.Connection, stock_id: int) -> float | None:
    r = conn.execute(
        """SELECT COALESCE(adj_close, close) FROM stock_daily_quotes
           WHERE stock_id=? AND COALESCE(adj_close, close) IS NOT NULL
           ORDER BY trade_date DESC LIMIT 1""",
        (stock_id,),
    ).fetchone()
    return float(r[0]) if r and r[0] is not None else None


def compute_attribution(portfolio_id: int, conn: sqlite3.Connection | None = None) -> dict:
    """每只持过的股对组合总收益的贡献(百分点)= 该股(已实现+未实现)盈亏 / 初始资金。"""
    conn, close = _conn(conn)
    try:
        pf = conn.execute(
            "SELECT initial_cash, cash FROM portfolios WHERE id=?", (portfolio_id,)
        ).fetchone()
        if not pf:
            return {"error": "组合不存在"}
        initial = float(pf["initial_cash"] or 0) or 1.0

        journal = _journal(conn, portfolio_id)
        realized = _per_stock_realized(journal)

        # 现持仓未实现盈亏
        positions = conn.execute(
            """SELECT p.stock_id, s.code, s.name, p.shares, p.avg_cost
               FROM portfolio_positions p JOIN stocks s ON s.id=p.stock_id
               WHERE p.portfolio_id=? AND p.shares>0""",
            (portfolio_id,),
        ).fetchall()
        held_codes = set()
        rows: dict[str, dict] = {}
        for p in positions:
            code = p["code"]
            held_codes.add(code)
            cur = _latest_adj_close(conn, p["stock_id"])
            unreal = (cur - float(p["avg_cost"])) * int(p["shares"]) if cur is not None else 0.0
            rows[code] = {
                "code": code, "name": p["name"], "still_held": True,
                "unrealized": round(unreal, 2),
                "realized": round(realized.get(code, {}).get("realized", 0.0), 2),
            }
        # 已完全平仓的股(只在 realized 里,不在现持仓)
        for code, rec in realized.items():
            if code not in rows:
                rows[code] = {
                    "code": code, "name": rec.get("name", code), "still_held": False,
                    "unrealized": 0.0, "realized": round(rec["realized"], 2),
                }

        out = []
        for r in rows.values():
            total = round(r["realized"] + r["unrealized"], 2)
            out.append({**r, "total_pnl": total,
                        "contribution_pp": round(total / initial * 100, 3)})
        out.sort(key=lambda x: -x["total_pnl"])

        total_pnl = round(sum(r["total_pnl"] for r in out), 2)
        return {
            "portfolio_id": portfolio_id,
            "initial_cash": initial,
            "total_pnl": total_pnl,
            "total_contribution_pp": round(total_pnl / initial * 100, 3),
            "winners": [r for r in out if r["total_pnl"] > 0],
            "losers": [r for r in out if r["total_pnl"] < 0],
            "rows": out,
        }
    finally:
        if close:
            conn.close()


def _pool_return(conn: sqlite3.Connection, start: str, end: str) -> float | None:
    """等权自选池在 [start, end] 的收益率:各活跃股 (end价/start价 - 1) 的均值。"""
    rows = conn.execute(
        """SELECT a.stock_id,
                  (SELECT COALESCE(adj_close,close) FROM stock_daily_quotes
                   WHERE stock_id=a.stock_id AND trade_date>=? AND COALESCE(adj_close,close) IS NOT NULL
                   ORDER BY trade_date ASC LIMIT 1) s0,
                  (SELECT COALESCE(adj_close,close) FROM stock_daily_quotes
                   WHERE stock_id=a.stock_id AND trade_date<=? AND COALESCE(adj_close,close) IS NOT NULL
                   ORDER BY trade_date DESC LIMIT 1) s1
           FROM (SELECT id AS stock_id FROM stocks WHERE is_active=1) a""",
        (start, end),
    ).fetchall()
    rets = [(float(r["s1"]) / float(r["s0"]) - 1) for r in rows if r["s0"] and r["s1"] and float(r["s0"]) > 0]
    return round(sum(rets) / len(rets) * 100, 2) if rets else None


def compute_position_perf(portfolio_id: int, code: str, conn: sqlite3.Connection | None = None) -> dict:
    """单票持有期分析:adj_close 归一曲线 + 沪深300 同期归一 + 关键点 + 超额。
    现持仓 → 买入至今;已平仓 → 买入至最后一笔卖出(closed=True)。"""
    conn, close = _conn(conn)
    try:
        pos = conn.execute(
            """SELECT p.stock_id, s.name FROM portfolio_positions p JOIN stocks s ON s.id=p.stock_id
               WHERE p.portfolio_id=? AND s.code=? AND p.shares>0""",
            (portfolio_id, code),
        ).fetchone()
        is_closed = pos is None
        if is_closed:
            srow = conn.execute("SELECT id, name FROM stocks WHERE code=?", (code,)).fetchone()
            if not srow:
                return {"error": "未知代码"}
            stock_id, name = int(srow["id"]), srow["name"]
        else:
            stock_id, name = int(pos["stock_id"]), pos["name"]

        buy_date = conn.execute(
            "SELECT MIN(trade_date) FROM trade_journal WHERE portfolio_id=? AND stock_id=? AND action='BUY'",
            (portfolio_id, stock_id),
        ).fetchone()[0]
        if not is_closed and not buy_date:
            bd = conn.execute(
                "SELECT MIN(buy_date) FROM portfolio_lots WHERE portfolio_id=? AND stock_id=?",
                (portfolio_id, stock_id),
            ).fetchone()
            buy_date = bd and bd[0]
        if not buy_date:
            return {"error": "无买入记录"}
        # 已平仓:曲线到最后一笔卖出日;现持仓:到最新交易日(无上界)
        end_cap = conn.execute(
            "SELECT MAX(trade_date) FROM trade_journal WHERE portfolio_id=? AND stock_id=? AND action='SELL'",
            (portfolio_id, stock_id),
        ).fetchone()[0] if is_closed else None

        bars = conn.execute(
            """SELECT trade_date, COALESCE(adj_close, close) c FROM stock_daily_quotes
               WHERE stock_id=? AND trade_date>=? AND (? IS NULL OR trade_date<=?)
                 AND COALESCE(adj_close, close) IS NOT NULL
               ORDER BY trade_date ASC""",
            (stock_id, buy_date, end_cap, end_cap),
        ).fetchall()
        if len(bars) < 2:
            return {"error": "行情不足"}
        base = float(bars[0]["c"])
        curve = [{"date": b["trade_date"], "v": round(float(b["c"]) / base * 100, 2)} for b in bars]
        end_date = bars[-1]["trade_date"]
        cur = float(bars[-1]["c"])
        stock_ret = round((cur / base - 1) * 100, 2)

        # 关键点:峰/谷(归一)、当前从峰值回撤
        vs = [x["v"] for x in curve]
        peak = max(vs); trough = min(vs)
        dd_from_peak = round((vs[-1] / peak - 1) * 100, 2) if peak else 0.0
        try:
            hold_days = (datetime.fromisoformat(end_date) - datetime.fromisoformat(buy_date)).days
        except ValueError:
            hold_days = len(bars)

        # 沪深300 同期归一曲线 + 超额
        csi_curve, csi_ret = [], None
        try:
            from services.market_index import fetch_index_kline
            idx = fetch_index_kline("sh000300", days=max(len(bars) + 40, 120))
            ik = [(k.get("date") or k.get("time"), k.get("close")) for k in (idx.get("kline") or idx.get("data") or [])]
            ik = [(d, float(v)) for d, v in ik if d and v and d >= buy_date and d <= end_date]
            ik.sort()
            if len(ik) >= 2:
                cb = ik[0][1]
                csi_curve = [{"date": d, "v": round(v / cb * 100, 2)} for d, v in ik]
                csi_ret = round((ik[-1][1] / cb - 1) * 100, 2)
        except Exception:
            pass

        pool_ret = _pool_return(conn, buy_date, end_date)
        # 加权平均买入价(展示用)
        buys = conn.execute(
            "SELECT shares, price FROM trade_journal WHERE portfolio_id=? AND stock_id=? AND action='BUY'",
            (portfolio_id, stock_id),
        ).fetchall()
        tot_sh = sum(int(b["shares"] or 0) for b in buys)
        avg_cost = (sum(int(b["shares"] or 0) * float(b["price"] or 0) for b in buys) / tot_sh) if tot_sh else base
        return {
            "code": code, "name": name, "closed": is_closed, "buy_date": buy_date, "end_date": end_date,
            "hold_days": hold_days, "avg_cost": round(avg_cost, 3),
            "current": round(cur, 3), "stock_return_pct": stock_ret,
            "peak_pct": round(peak - 100, 2), "trough_pct": round(trough - 100, 2),
            "drawdown_from_peak_pct": dd_from_peak,
            "csi300_return_pct": csi_ret,
            "excess_vs_csi300_pp": round(stock_ret - csi_ret, 2) if csi_ret is not None else None,
            "pool_return_pct": pool_ret,
            "excess_vs_pool_pp": round(stock_ret - pool_ret, 2) if pool_ret is not None else None,
            "curve": curve,
            "csi300_curve": csi_curve,
        }
    finally:
        if close:
            conn.close()
