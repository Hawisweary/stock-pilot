"""模拟盘分析 — 指标、vs 回测、导出、建仓预览"""
from __future__ import annotations

import csv
import io
import sqlite3
from datetime import date, datetime
from typing import Optional

from config import DB_PATH
from services.backtest_engine import run_backtest
from services.portfolio_svc import get_portfolio
from services.trade_pricing import apply_slippage, get_market_context, pricing_context_dict, resolve_trade_price
from services.trading_rules import COMMISSION, STAMP_TAX, split_cost
from services.strategy_registry import get_meta, is_valid_strategy, normalize_strategy_id
from services.strategy_selector import select_top_n_dicts


def _normalize_curve(points: list[dict], date_key: str = "date", val_key: str = "value") -> list[dict]:
    if not points:
        return []
    base = points[0].get(val_key) or points[0].get("total_value") or 0
    if base <= 0:
        return points
    out = []
    for p in points:
        v = p.get(val_key, p.get("total_value", 0))
        d = p.get(date_key, p.get("snapshot_date", ""))
        out.append({"date": d, "value": round(v / base * 100, 2)})
    return out


def _align_curves(sim: list[dict], bt: list[dict]) -> tuple[list[dict], list[dict]]:
    bt_map = {p["date"]: p["value"] for p in bt}
    sim_map = {p["date"]: p["value"] for p in sim}
    common = sorted(set(bt_map) & set(sim_map))
    if not common:
        return sim, bt
    s0, b0 = sim_map[common[0]], bt_map[common[0]]
    if s0 <= 0 or b0 <= 0:
        return sim, bt
    return (
        [{"date": d, "value": round(sim_map[d] / s0 * 100, 2)} for d in common],
        [{"date": d, "value": round(bt_map[d] / b0 * 100, 2)} for d in common],
    )


def compute_metrics(portfolio_id: int) -> dict:
    pf = get_portfolio(portfolio_id)
    if "error" in pf:
        return pf

    history = pf.get("history") or []
    initial = float(pf.get("initial_cash") or 100000)
    final = float(pf.get("total_value") or initial)
    total_return = round((final / initial - 1) * 100, 2) if initial else 0

    max_dd = 0.0
    peak = initial
    for h in history:
        v = float(h.get("total_value", 0))
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

    created = pf.get("created_at") or date.today().isoformat()
    try:
        days_running = (date.today() - date.fromisoformat(created[:10])).days
    except ValueError:
        days_running = len(history)

    journal_stats = _journal_stats(pf.get("journal") or [])

    return {
        "portfolio_id": portfolio_id,
        "total_return_pct": total_return,
        "max_drawdown_pct": round(max_dd * 100, 2),
        "days_running": days_running,
        "win_rate_pct": journal_stats["win_rate_pct"],
        "avg_hold_days": journal_stats["avg_hold_days"],
        "realized_pnl": journal_stats["realized_pnl"],
        "closed_trades": journal_stats["closed_trades"],
        "total_trades": journal_stats["total_trades"],
    }


def _journal_stats(journal: list[dict]) -> dict:
    """FIFO 配对买卖，统计已实现盈亏（口径：买入含佣成本价，卖出扣佣+印花税）"""
    from services.trading_rules import COMMISSION, STAMP_TAX

    lots: dict[str, list[dict]] = {}
    closed: list[dict] = []
    for j in sorted(journal, key=lambda x: x.get("trade_date", "")):
        code = j.get("code", "")
        action = (j.get("action") or "").upper()
        sh = int(j.get("shares") or 0)
        price = float(j.get("price") or 0)
        td = j.get("trade_date", "")
        if action == "BUY":
            cost_price = price * (1 + COMMISSION)  # 含佣成本价（与 avg_cost 口径一致）
            lots.setdefault(code, []).append({"shares": sh, "price": cost_price, "date": td})
        elif action == "SELL" and code in lots:
            net_sell = price * (1 - COMMISSION - STAMP_TAX)  # 卖出净所得每股
            rem = sh
            while rem > 0 and lots[code]:
                lot = lots[code][0]
                take = min(rem, lot["shares"])
                pnl = take * (net_sell - lot["price"])
                try:
                    hold = (datetime.fromisoformat(td) - datetime.fromisoformat(lot["date"])).days
                except ValueError:
                    hold = 0
                closed.append({"pnl": pnl, "hold_days": hold})
                lot["shares"] -= take
                rem -= take
                if lot["shares"] <= 0:
                    lots[code].pop(0)

    if not closed:
        return {
            "win_rate_pct": 0,
            "avg_hold_days": 0,
            "realized_pnl": 0,
            "closed_trades": 0,
            "total_trades": len(journal),
        }
    wins = sum(1 for c in closed if c["pnl"] > 0)
    return {
        "win_rate_pct": round(wins / len(closed) * 100, 1),
        "avg_hold_days": round(sum(c["hold_days"] for c in closed) / len(closed), 1),
        "realized_pnl": round(sum(c["pnl"] for c in closed), 2),
        "closed_trades": len(closed),
        "total_trades": len(journal),
    }


def compare_with_backtest(
    portfolio_id: int,
    days: int = 90,
    top_n: int = 5,
    min_score: float = 50,
    strategy: str = "composite",
    pos_style: str = "equal",
    rebalance: str = "weekly",
) -> dict:
    pf = get_portfolio(portfolio_id)
    if "error" in pf:
        return pf

    history = pf.get("history") or []
    if len(history) < 2:
        return {"error": "模拟盘快照不足（至少2天），请先运行或交易"}

    sim_days = min(days, len(history))
    strategy = normalize_strategy_id(strategy)
    if not is_valid_strategy(strategy):
        return {"error": f"未知策略: {strategy}"}
    meta = get_meta(strategy)
    strategy_key = meta.id if meta else strategy
    bt_kwargs: dict = {
        "days": sim_days,
        "top_n": top_n,
        "min_score": min_score,
        "strategy": strategy_key,
        "pos_style": pos_style,
        "rebalance": rebalance,
    }
    if strategy_key == "factor_combination":
        return {"error": "对比回测暂不支持 factor_combination，请用单因子或 V5 策略"}

    bt = run_backtest(**bt_kwargs)
    if "error" in bt:
        return bt

    sim_raw = [
        {"date": h["snapshot_date"], "value": float(h["total_value"])}
        for h in history[-sim_days:]
    ]
    bt_raw = bt.get("daily_values") or []
    sim_aligned, bt_aligned = _align_curves(
        _normalize_curve(sim_raw),
        _normalize_curve(bt_raw),
    )
    bench = _normalize_curve(bt.get("benchmark_curve") or [])

    sim_ret = round((sim_raw[-1]["value"] / sim_raw[0]["value"] - 1) * 100, 2) if sim_raw else 0
    bt_ret = bt.get("total_return_pct", 0)

    return {
        "portfolio_id": portfolio_id,
        "params": {
            "days": sim_days,
            "top_n": top_n,
            "min_score": min_score,
            "strategy": strategy_key,
            "pos_style": pos_style,
            "rebalance": rebalance,
        },
        "simulated_curve": sim_aligned,
        "backtest_curve": bt_aligned,
        "benchmark_curve": bench,
        "sim_return_pct": sim_ret,
        "backtest_return_pct": bt_ret,
        "gap_pct": round(sim_ret - float(bt_ret), 2),
        "backtest_metrics": {
            "sharpe": bt.get("sharpe"),
            "max_drawdown_pct": bt.get("max_drawdown_pct"),
            "win_rate_pct": bt.get("win_rate_pct"),
        },
    }


def preview_build_top(
    portfolio_id: int,
    top_n: int = 5,
    min_score: float = 50,
    strategy: str = "composite",
    pos_style: str = "equal",
    combination_id: int | None = None,
    lookback: int = 20,
    sector_window: int = 5,
    per_sector: int = 2,
) -> dict:
    strategy = normalize_strategy_id(strategy)
    if not is_valid_strategy(strategy, combination_id=combination_id):
        return {"error": f"未知策略: {strategy}"}

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    pf = conn.execute(
        "SELECT cash, max_weight_pct FROM portfolios WHERE id=?", (portfolio_id,)
    ).fetchone()
    if not pf:
        conn.close()
        return {"error": "组合不存在"}
    max_w = float(pf["max_weight_pct"] or 30) / 100.0

    selected, err = select_top_n_dicts(
        conn=conn,
        strategy=strategy,
        top_n=top_n,
        min_score=min_score,
        combination_id=combination_id,
        lookback=lookback,
        sector_window=sector_window,
        per_sector=per_sector,
    )
    if err:
        conn.close()
        return {"error": err}
    rows = selected

    cash = float(pf["cash"])
    total_score = sum(float(r["score"]) for r in rows)
    weights: dict[str, float] = {}
    for r in rows:
        if pos_style == "weighted" and total_score:
            weights[r["code"]] = float(r["score"]) / total_score
        else:
            weights[r["code"]] = 1.0 / len(rows)
    for code in list(weights.keys()):
        weights[code] = min(weights[code], max_w)

    preview = []
    for r in rows:
        tq = resolve_trade_price(r["code"], int(r["stock_id"]), "buy", conn, for_display=True)
        exec_price = apply_slippage(tq.price, "buy") if tq.price else 0
        w = weights[r["code"]]
        budget = cash * w
        shares = max(0, int(budget / (exec_price * (1 + COMMISSION)) / 100) * 100) if exec_price > 0 else 0
        preview.append({
            "code": r["code"],
            "name": r["name"],
            "score": round(float(r["score"]), 1),
            "price": exec_price,
            "raw_price": tq.raw_price,
            "quote_date": tq.quote_date,
            "price_source": tq.source,
            "price_label": tq.label,
            "weight_pct": round(w * 100, 1),
            "shares": shares,
            "est_cost": round(shares * exec_price * (1 + COMMISSION), 2) if shares else 0,
        })
    conn.close()

    return {
        "portfolio_id": portfolio_id,
        "cash": cash,
        "preview": preview,
        "strategy": strategy,
        "pos_style": pos_style,
        "pricing": pricing_context_dict(),
    }


def estimate_trade_fees(code: str, action: str, shares: int) -> dict:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT id FROM stocks WHERE code=? AND is_active=1", (code,),
    ).fetchone()
    if not row:
        conn.close()
        return {"error": "股票不在跟踪列表"}
    ctx = get_market_context(conn)
    tq = resolve_trade_price(code, row[0], action, conn, for_display=True)
    conn.close()
    if tq.error and tq.price <= 0:
        return {"error": tq.error or "无行情"}
    exec_price = apply_slippage(tq.price, action.lower())
    amount = shares * exec_price
    cash_delta, commission, tax = split_cost(amount, action.lower())
    return {
        "code": code,
        "action": action,
        "shares": shares,
        "price": exec_price,
        "raw_price": tq.raw_price,
        "quote_date": tq.quote_date,
        "price_source": tq.source,
        "price_label": tq.label,
        "amount": round(amount, 2),
        "commission": commission,
        "tax": tax,
        "cash_delta": round(cash_delta, 2),
        "can_trade": ctx.can_trade,
        "block_reason": ctx.block_reason,
        "market_mode": ctx.mode,
    }


def export_portfolio_csv(portfolio_id: int) -> str:
    pf = get_portfolio(portfolio_id)
    if "error" in pf:
        raise ValueError(pf["error"])

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["# 组合", pf.get("name"), f"ID={portfolio_id}"])
    w.writerow(["总资产", pf.get("total_value"), "现金", pf.get("cash"), "盈亏%", pf.get("pnl_pct")])
    w.writerow([])
    w.writerow(["持仓", "代码", "名称", "股数", "成本", "现价", "盈亏%"])
    for p in pf.get("positions") or []:
        w.writerow(["", p.get("code"), p.get("name"), p.get("shares"), p.get("avg_cost"), p.get("price"), p.get("pnl_pct")])
    w.writerow([])
    w.writerow(["流水", "日期", "代码", "动作", "股数", "价格", "佣金", "税"])
    for j in pf.get("journal") or []:
        w.writerow(["", j.get("trade_date"), j.get("code"), j.get("action"), j.get("shares"), j.get("price"),
                    j.get("commission"), j.get("tax")])
    w.writerow([])
    w.writerow(["快照", "日期", "总资产"])
    for h in pf.get("history") or []:
        w.writerow(["", h.get("snapshot_date"), h.get("total_value")])
    return buf.getvalue()
