from config import DB_PATH, KLINE_DISPLAY_DAYS

"""
技术面分析 API - Ashare行情 + MyTT技术指标
GET /api/stocks/{id}/technical → MACD/KDJ/RSI/BOLL/ATR/MA/SLOPE
"""
from fastapi import APIRouter, HTTPException
from api_utils import execute_sql
import numpy as np

router = APIRouter(prefix="/api", tags=["technical"])


@router.get("/stocks/{stock_id}/technical")
async def get_technical(stock_id: int, days: int = KLINE_DISPLAY_DAYS):
    stock = execute_sql("SELECT code, name FROM stocks WHERE id=?", (stock_id,))
    if not stock: raise HTTPException(status_code=404)

    code = stock[0]["code"]
    name = stock[0]["name"]
    ash_code = f"sh{code}" if code.startswith(("6","9")) else f"sz{code}"

    try:
        from services.Ashare import get_price
        df = get_price(ash_code, frequency='1d', count=days)
        if df is None or df.empty:
            return {"error": "无法获取行情", "data": [], "degraded": True}

        closes = np.array(df['close'].values, dtype=float)
        highs = np.array(df['high'].values, dtype=float)
        lows = np.array(df['low'].values, dtype=float)
        opens = np.array(df['open'].values, dtype=float)
        volumes = np.array(df['volume'].values, dtype=float)

        from services.MyTT import MACD, KDJ, RSI, BOLL, ATR, MA
        macd_dif, macd_dea, macd_bar = MACD(closes)
        k, d, j = KDJ(closes, highs, lows)
        rsi = RSI(closes, 14)
        upper, mid, lower = BOLL(closes)
        atr = ATR(closes, highs, lows, 14)
        ma5 = MA(closes, 5)
        ma10 = MA(closes, 10)
        ma20 = MA(closes, 20)
        # rolling 10-period slope
        slope = np.full(len(closes), np.nan)
        for i in range(9, len(closes)):
            slope[i] = np.polyfit(np.arange(10), closes[i-9:i+1], 1)[0]

        dates = df.index.strftime('%Y-%m-%d').tolist()
        result = []
        for i in range(len(dates)):
            result.append({
                "date": dates[i],
                "open": _s(opens[i]), "high": _s(highs[i]), "low": _s(lows[i]),
                "close": _s(closes[i]), "volume": _s(volumes[i]),
                "ma5": _s(ma5[i]), "ma10": _s(ma10[i]), "ma20": _s(ma20[i]),
                "macd_dif": _s(macd_dif[i]), "macd_dea": _s(macd_dea[i]), "macd_bar": _s(macd_bar[i]),
                "kdj_k": _s(k[i]), "kdj_d": _s(d[i]), "kdj_j": _s(j[i]),
                "rsi14": _s(rsi[i]),
                "boll_upper": _s(upper[i]), "boll_mid": _s(mid[i]), "boll_lower": _s(lower[i]),
                "atr14": _s(atr[i]), "slope10": _s(slope[i]),
            })
        return {"stock_id": stock_id, "code": code, "name": name, "data": result, "count": len(result)}
    except Exception as e:
        import logging; logging.getLogger(__name__).exception("technical indicators failed stock_id=%s", stock_id)
        return {"error": str(e)[:200], "data": [], "degraded": True}


@router.get("/stocks/{stock_id}/technical/cache")
async def get_cached_technical_analysis(stock_id: int):
    """获取缓存的 AI 技术面分析（不触发 LLM）"""
    try:
        import sqlite3, json
        from services.tech_ai import _hash_input, compute_technical_indicators
        from services.Ashare import get_price
        import numpy as np

        stock = execute_sql("SELECT code FROM stocks WHERE id=?", (stock_id,))
        if not stock: raise HTTPException(status_code=404)
        code = stock[0]["code"]
        ash_code = f"sh{code}" if code.startswith(("6","9")) else f"sz{code}"

        df_d = get_price(ash_code, frequency='1d', count=KLINE_DISPLAY_DAYS)
        if df_d is None or df_d.empty:
            return {"cached": False, "analysis": None}

        daily = compute_technical_indicators(
            df_d['close'].values, df_d['high'].values, df_d['low'].values, df_d['volume'].values
        )
        daily["close"] = round(float(df_d['close'].values[-1]), 2)

        df_w = get_price(ash_code, frequency='1w', count=60)
        weekly = {}
        if df_w is not None and not df_w.empty:
            weekly = compute_technical_indicators(
                df_w['close'].values, df_w['high'].values, df_w['low'].values
            )
            weekly["close"] = round(float(df_w['close'].values[-1]), 2)

        from services.market_index import fetch_market_index_snapshot

        market_snapshot = fetch_market_index_snapshot()
        input_hash = _hash_input(daily, weekly, market_snapshot)

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT full_result, created_at FROM tech_analysis_cache WHERE stock_id=? AND input_hash=?",
            (stock_id, input_hash)
        ).fetchone()
        conn.close()

        if row:
            result = json.loads(row["full_result"])
            result["cached"] = True
            result["cached_at"] = row["created_at"]
            return {"cached": True, "analysis": result}
        return {"cached": False, "analysis": None}
    except Exception as e:
        return {"cached": False, "analysis": None, "error": str(e)[:100]}


@router.get("/stocks/{stock_id}/technical/weekly")
async def get_weekly_indicators(stock_id: int, weeks: int = 60):
    """获取周线技术指标"""
    stock = execute_sql("SELECT code FROM stocks WHERE id=?", (stock_id,))
    if not stock: raise HTTPException(status_code=404)
    code = stock[0]["code"]
    ash_code = f"sh{code}" if code.startswith(("6","9")) else f"sz{code}"

    try:
        from services.Ashare import get_price
        df = get_price(ash_code, frequency='1w', count=weeks)
        if df is None or df.empty:
            return {"data": []}

        closes = np.array(df['close'].values, dtype=float)
        highs = np.array(df['high'].values, dtype=float)
        lows = np.array(df['low'].values, dtype=float)

        from services.MyTT import MACD, KDJ, RSI, BOLL, ATR, MA
        macd_dif, macd_dea, macd_bar = MACD(closes)
        k, d, j = KDJ(closes, highs, lows)
        rsi = RSI(closes, 14)
        upper, mid, lower = BOLL(closes)
        atr = ATR(closes, highs, lows, 14)

        dates = df.index.strftime('%Y-%m-%d').tolist()
        result = []
        for i in range(len(dates)):
            result.append({
                "date": dates[i],
                "close": _s(closes[i]),
                "rsi14": _s(rsi[i]),
                "macd_dif": _s(macd_dif[i]), "macd_dea": _s(macd_dea[i]), "macd_bar": _s(macd_bar[i]),
                "kdj_k": _s(k[i]), "kdj_d": _s(d[i]), "kdj_j": _s(j[i]),
                "boll_upper": _s(upper[i]), "boll_mid": _s(mid[i]), "boll_lower": _s(lower[i]),
                "atr14": _s(atr[i]),
            })
        # 返回最新一期 + 摘要
        latest = result[-1] if result else {}
        latest["ma5"] = _s(MA(closes, 5)[-1])
        latest["ma10"] = _s(MA(closes, 10)[-1])
        latest["ma20"] = _s(MA(closes, 20)[-1])
        return {"data": result[-20:], "latest": latest, "count": len(result)}
    except Exception as e:
        return {"error": str(e)[:200], "data": [], "degraded": True}


@router.post("/stocks/{stock_id}/technical/analyze")
async def analyze_technical_deep(stock_id: int):
    """AI 深度技术面多周期分析"""
    stock = execute_sql("SELECT code, name FROM stocks WHERE id=?", (stock_id,))
    if not stock: raise HTTPException(status_code=404)
    code, name = stock[0]["code"], stock[0]["name"]
    ash_code = f"sh{code}" if code.startswith(("6","9")) else f"sz{code}"

    try:
        from services.Ashare import get_price
        from services.market_index import fetch_market_index_snapshot
        from services.tech_ai import compute_technical_indicators, analyze_technical

        market_snapshot = fetch_market_index_snapshot()

        # 日线
        df_d = get_price(ash_code, frequency='1d', count=KLINE_DISPLAY_DAYS)
        if df_d is None or df_d.empty:
            return {"error": "无法获取日线数据", "degraded": True}

        daily = compute_technical_indicators(
            df_d['close'].values, df_d['high'].values, df_d['low'].values, df_d['volume'].values
        )
        daily["close"] = round(float(df_d['close'].values[-1]), 2)
        # 附加原始价格序列供规则引擎使用
        daily["closes"] = [round(float(x), 2) for x in df_d['close'].values[-60:]]

        # 周线
        df_w = get_price(ash_code, frequency='1w', count=60)
        weekly = {"close": 0, "closes": []}
        if df_w is not None and not df_w.empty:
            weekly = compute_technical_indicators(
                df_w['close'].values, df_w['high'].values, df_w['low'].values
            )
            weekly["close"] = round(float(df_w['close'].values[-1]), 2)
            weekly["closes"] = [round(float(x), 2) for x in df_w['close'].values[-10:]]

        result = analyze_technical(
            daily, weekly, name, stock_id, market_snapshot=market_snapshot
        )
        try:
            from services.comprehensive_store import upsert_dimension_score
            import logging
            tech_score = result.get("score", 50)
            upsert_dimension_score(stock_id, "technical_score", tech_score)
        except Exception as e:
            logging.getLogger(__name__).warning("technical score persist failed stock=%s: %s", stock_id, e)
        return {
            "stock_id": stock_id,
            "code": code,
            "name": name,
            "analysis": result,
            "cached": result.get("cached", False),
            "daily_latest": daily,
            "weekly_latest": weekly,
            "market_indices": market_snapshot,
        }
    except Exception as e:
        return {"error": str(e)[:200], "degraded": True}


@router.post("/technical/analyze-all")
async def analyze_all_technical():
    """批量触发全量技术面AI分析（后台线程，跳过已有缓存）"""
    import threading, time as _time

    def _run():
        stocks = execute_sql("SELECT id, code, name FROM stocks WHERE is_active=1")
        print(f"[TechAI] 开始全量技术面分析 {len(stocks)} 只")
        from services.Ashare import get_price
        from services.market_index import fetch_market_index_snapshot
        from services.tech_ai import compute_technical_indicators, analyze_technical
        from services.comprehensive_store import upsert_dimension_score

        market_snapshot = fetch_market_index_snapshot()
        for i, s in enumerate(stocks):
            try:
                ash_code = f"sh{s['code']}" if s['code'].startswith(("6","9")) else f"sz{s['code']}"
                df_d = get_price(ash_code, frequency='1d', count=KLINE_DISPLAY_DAYS)
                if df_d is None or df_d.empty:
                    continue
                daily = compute_technical_indicators(
                    df_d['close'].values, df_d['high'].values, df_d['low'].values, df_d['volume'].values
                )
                daily["close"] = round(float(df_d['close'].values[-1]), 2)

                df_w = get_price(ash_code, frequency='1w', count=60)
                weekly = {}
                if df_w is not None and not df_w.empty:
                    weekly = compute_technical_indicators(
                        df_w['close'].values, df_w['high'].values, df_w['low'].values
                    )
                    weekly["close"] = round(float(df_w['close'].values[-1]), 2)

                result = analyze_technical(
                    daily, weekly, s["name"], s["id"], market_snapshot=market_snapshot
                )
                score = result.get("score")
                if score is not None:
                    upsert_dimension_score(s["id"], "technical_score", float(score))
                tag = "缓存" if result.get("cached") else "新算"
                print(f"[TechAI] {i+1}/{len(stocks)} {s['code']} {tag} score={score}")
                _time.sleep(1.5)
            except Exception as e:
                print(f"[TechAI] {s['code']} 失败: {e}")
        print("[TechAI] 全量完成")

    threading.Thread(target=_run, daemon=True).start()
    total = len(execute_sql("SELECT id FROM stocks WHERE is_active=1"))
    return {"status": "started", "message": "全量技术面分析已启动", "total": total}


def _s(v):
    try:
        if v is None or (isinstance(v, float) and np.isnan(v)): return None
        return round(float(v), 4)
    except: return None
