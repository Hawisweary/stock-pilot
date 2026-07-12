"""资金面评分引擎 — 全局百分位，区分新股/低成交。

V5 资金面主路径：主力 5 日 + 两融 + 龙虎榜（stock_fund_flow_daily 等）。
北向日频净买自 2024-08 停更，不进 V5 硬打分（仅 GET /api/market/northbound 展示）。
"""
import sqlite3, json, traceback
from datetime import date, datetime
from config import DB_PATH

def _db():
    return sqlite3.connect(DB_PATH)


def compute_all_capital(date_str: str = None):
    """批量计算54股资金分（基于全局百分位）"""
    if not date_str:
        date_str = date.today().strftime("%Y-%m-%d")

    conn = _db()
    now = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    debug = open(f"/tmp/capital_{now}.log", "w")

    try:
        # 1. 获取所有股票
        stocks_raw = conn.execute(
            "SELECT id, code, name FROM stocks WHERE is_active=1"
        ).fetchall()
        print(f"[capital] total stocks: {len(stocks_raw)}", file=debug)

        # 统一转成 dict，避免 tuple/dict 混淆
        stocks = []
        for s in stocks_raw:
            if isinstance(s, dict):
                stocks.append(s)
            else:
                stocks.append({"id": s[0], "code": s[1], "name": s[2] if len(s) > 2 else ""})

        codes = [s["code"] for s in stocks]

        # 2. 获取实时行情（分批200只避免URL过长）
        from services.data_sources import tencent_quote
        CHUNK = 200
        quotes = {}
        for i in range(0, len(codes), CHUNK):
            quotes.update(tencent_quote(codes[i:i + CHUNK]))
        print(f"[capital] quotes keys: {len(quotes)}", file=debug)

        # 3. 逐股计算指标
        stock_data = []
        for s in stocks:
            stock_id = s["id"]
            code     = s["code"]
            name     = s.get("name", "")
            q = quotes.get(code)
            if not q:
                print(f"[capital] SKIP {code} {name}: no quote", file=debug)
                continue

            try:
                # 历史30天数据
                hist_raw = conn.execute(
                    """SELECT close, volume, turnover FROM stock_daily_quotes
                       WHERE stock_id=? AND close IS NOT NULL ORDER BY trade_date DESC LIMIT 30""",
                    (stock_id,)
                ).fetchall()
                if len(hist_raw) < 5:
                    print(f"[capital] SKIP {code}: only {len(hist_raw)} days hist", file=debug)
                    continue

                # 统一转成 list（避免 sqlite3.Row / tuple 差异）
                hist = []
                for r in reversed(hist_raw):
                    if isinstance(r, dict):
                        hist.append(r)
                    else:
                        hist.append({"close": r[0], "volume": r[1], "turnover": r[2]})

                closes   = [h["close"]   for h in hist]
                volumes  = [h["volume"]  or 0 for h in hist]
                turnovers = [h["turnover"] or 0 for h in hist]

                vol_ma5  = sum(volumes[-5:]) / 5
                vol_ma20 = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else sum(volumes) / len(volumes)
                vol_level = vol_ma5 / vol_ma20 if vol_ma20 > 0 else 1

                ma5  = sum(closes[-5:]) / 5
                ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else sum(closes) / len(closes)
                momentum = ma5 / ma20 - 1 if ma20 > 0 else 0

                # 换手率
                q_turn = q.get("turnover_pct", 0) or 0
                avg_turn5 = sum(turnovers[-5:]) / 5 if turnovers else 5
                current_turn = q_turn if q_turn > 0 else avg_turn5

                change_pct = q.get("change_pct", 0) or 0
                valid_turns = [t for t in turnovers if t > 0]
                avg_turn = sum(valid_turns) / len(valid_turns) if valid_turns else 3.0

                # 新股 / 低成交检测
                is_new = False
                avg_amount = 0
                try:
                    row = conn.execute("SELECT list_date FROM stocks WHERE id=?", (stock_id,)).fetchone()
                    if row:
                        ld = row[0] if not isinstance(row, dict) else row.get("list_date")
                        if ld:
                            days_listed = (date.today() - date.fromisoformat(str(ld)[:10])).days
                            is_new = days_listed < 60
                    price = q.get("price", 0) or 0
                    avg_vol = sum(volumes[-5:]) / min(len(volumes), 5)
                    avg_amount = price * avg_vol
                except Exception as e:
                    print(f"[capital] new-stock check failed {code}: {e}", file=debug)

                stock_data.append({
                    "stock_id": stock_id, "code": code,
                    "vol_level": vol_level, "momentum": momentum,
                    "turnover": current_turn, "avg_turn": avg_turn,
                    "change_pct": change_pct, "price": q.get("price", 0) or 0,
                    "vol_ratio": q.get("vol_ratio", 1) or 1,
                    "is_new_stock": is_new, "avg_amount": avg_amount,
                })

            except Exception as e:
                print(f"[capital] ERROR stock_id={stock_id} code={code}: {e}", file=debug)
                traceback.print_exc(file=debug)
                continue

        if not stock_data:
            print("[capital] NO stock_data, returning empty", file=debug)
            debug.close()
            conn.close()
            return []

        # 4. 全局百分位
        vol_levels   = sorted([d["vol_level"] for d in stock_data])
        momentums   = sorted([d["momentum"] for d in stock_data])
        turnovers_all = sorted([d["turnover"] for d in stock_data])

        results = []
        for d in stock_data:
            vl_rank = sum(1 for v in vol_levels   if v <= d["vol_level"])   / len(vol_levels)
            mo_rank = sum(1 for v in momentums   if v <= d["momentum"])   / len(momentums)
            tu_rank = sum(1 for v in turnovers_all if v <= d["turnover"]) / len(turnovers_all)

            # 换手率折损
            turnover_penalty = 1.0
            if d.get("is_new_stock"):
                turnover_penalty = 0.5
            elif d.get("avg_amount", 0) < 20_000_000:
                turnover_penalty = 0.7
            tu_rank_adj = tu_rank * turnover_penalty + (1 - turnover_penalty) * 0.5

            # 资金流评分
            flow = min(100, max(0, (vl_rank * 0.5 + d["momentum"] * 10 + 0.5) * 100))
            # 换手评分
            if 0.5 <= tu_rank_adj <= 0.8:
                turn_score = 70 + (tu_rank_adj - 0.5) * 30
            elif tu_rank_adj > 0.9:
                turn_score = max(10, 70 - (tu_rank_adj - 0.9) * 200)
            elif tu_rank_adj < 0.1:
                turn_score = max(10, 40 + tu_rank_adj * 100)
            else:
                turn_score = 60
            # 涨跌评分
            chg_pct = d["change_pct"]
            if chg_pct > 0:
                chg_score = 50 + min(abs(chg_pct) * 2, 15)
            else:
                chg_score = max(20, 50 + chg_pct * 2)
            # 合成
            composite = flow * 0.45 + turn_score * 0.30 + chg_score * 0.25
            composite = max(0, min(100, composite))
            results.append({
                "stock_id": d["stock_id"], "date": date_str,
                "composite_score": round(composite, 1), "score": round(composite, 1),
                "flow_score": round(flow, 1), "turnover_score": round(turn_score, 1),
                "volume_score": round(d["vol_level"] * 100, 1),
                "change_score": round(chg_score, 1), "holder_score": 0,
            })

        print(f"[capital] DONE: {len(results)} results", file=debug)
        debug.close()
        conn.close()
        return results

    except Exception as e:
        print(f"[capital] FATAL: {e}", file=debug)
        traceback.print_exc(file=debug)
        debug.close()
        conn.close()
        return []


def compute_capital_score(stock_id: int, code: str) -> dict:
    """单股资金分 — 优先读缓存"""
    from services.score_cache import get_latest_dimension

    cached = get_latest_dimension("capital_scores", stock_id)
    if cached and cached.get("composite_score") is not None:
        return {
            "stock_id": stock_id,
            "date": cached.get("date"),
            "score": cached["composite_score"],
            "composite_score": cached["composite_score"],
            "flow_score": cached.get("flow_score", 0),
            "turn_score": cached.get("turnover_score", 0),
            "turnover_score": cached.get("turnover_score", 0),
            "volume_score": cached.get("volume_score", 0),
            "change_score": cached.get("change_score", 0),
            "holder_score": cached.get("holder_score", 0),
        }
    for r in compute_all_capital():
        if r["stock_id"] == stock_id:
            return r
    return {"error": "资金数据不足"}
