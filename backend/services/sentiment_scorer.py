"""情绪面评分引擎 — 连续百分位，全股跨截面排名"""
import math
import sqlite3
from collections import defaultdict
from datetime import date

from config import DB_PATH
from services.score_cache import get_latest_dimension


def _load_quote_hist(conn: sqlite3.Connection) -> dict[int, list]:
    """一次查询加载全市场近 60 日行情，按 stock_id 分组。"""
    rows = conn.execute(
        """SELECT stock_id, trade_date, close, volume, turnover
           FROM stock_daily_quotes
           WHERE stock_id IN (SELECT id FROM stocks WHERE is_active=1)
             AND close IS NOT NULL
             AND trade_date >= date('now', '-120 days')
           ORDER BY stock_id, trade_date DESC"""
    ).fetchall()
    grouped: dict[int, list] = defaultdict(list)
    for r in rows:
        sid = r["stock_id"]
        if len(grouped[sid]) < 60:
            grouped[sid].append(dict(r))
    for sid in grouped:
        grouped[sid].reverse()
    return grouped


def compute_all_sentiment(date_str: str = None):
    """批量计算全股情绪分（全局分位）"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    if not date_str:
        date_str = date.today().strftime("%Y-%m-%d")

    stocks = conn.execute("SELECT id, code FROM stocks WHERE is_active=1").fetchall()
    hist_map = _load_quote_hist(conn)
    conn.close()

    stock_data = []
    for s in stocks:
        hist = hist_map.get(s["id"], [])
        if len(hist) < 5:
            continue
        closes = [r["close"] for r in hist]
        turnovers = [r["turnover"] or 0 for r in hist[-5:]]
        rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
        if len(rets) < 5:
            stock_data.append({"stock_id": s["id"], "code": s["code"], "turnover": 0, "volatility": 5, "momentum": 0})
            continue

        vol_20 = math.sqrt(sum(r**2 for r in rets[-min(20, len(rets)) :]) / min(20, len(rets))) * 100
        mom_5 = sum(rets[-5:]) * 100 if len(rets) >= 5 else 0
        mom_20 = sum(rets[-20:]) * 100 if len(rets) >= 20 else mom_5
        momentum = mom_5 * 0.3 + mom_20 * 0.7
        stock_data.append(
            {
                "stock_id": s["id"],
                "code": s["code"],
                "turnover": sum(turnovers) / max(len(turnovers), 1),
                "volatility": vol_20,
                "momentum": momentum,
            }
        )

    if len(stock_data) < 3:
        return []

    turnovers_all = sorted([d["turnover"] for d in stock_data])
    vols_all = sorted([d["volatility"] for d in stock_data])
    moms_all = sorted([d["momentum"] for d in stock_data])
    n = len(stock_data)

    results = []
    for d in stock_data:
        vo_rank = sum(1 for v in vols_all if v <= d["volatility"]) / n
        vol_score = vo_rank * 100
        mo_rank = sum(1 for m in moms_all if m <= d["momentum"]) / n
        mo_score = mo_rank * 100
        composite = vol_score * 0.30 + mo_score * 0.70
        composite = max(0, min(100, composite))
        results.append(
            {
                "stock_id": d["stock_id"],
                "date": date_str,
                "composite_score": round(composite, 1),
                "score": round(composite, 1),
                "vol_score": round(vol_score, 1),
            }
        )
    return results


def compute_sentiment_score(stock_id: int, code: str) -> dict:
    """单股情绪分 — 优先读缓存，避免全量重算"""
    cached = get_latest_dimension("sentiment_scores", stock_id)
    if cached and cached.get("composite_score") is not None:
        return {
            "stock_id": stock_id,
            "score": cached["composite_score"],
            "composite_score": cached["composite_score"],
            "vol_score": cached.get("leverage_score", 0),
            "turn_score": cached.get("turnover_score", 0),
        }
    for r in compute_all_sentiment():
        if r["stock_id"] == stock_id:
            r["score"] = r.get("composite_score", 0)
            return r
    return {"error": "情绪数据不足"}
