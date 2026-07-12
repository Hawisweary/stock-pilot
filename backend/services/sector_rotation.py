"""行业轮动 — 5 个交易日涨跌幅 + 相对跟踪池强度"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any

from config import DB_PATH, latest_trading_date
from services.score_sql import per_stock_latest_join


def _recent_trade_dates(conn: sqlite3.Connection, n_back: int = 5) -> tuple[str | None, str | None]:
    """返回 (最新交易日, n_back 个交易日前)。"""
    rows = conn.execute(
        """SELECT DISTINCT trade_date FROM stock_daily_quotes
           WHERE close IS NOT NULL ORDER BY trade_date DESC LIMIT ?""",
        (n_back + 1,),
    ).fetchall()
    if len(rows) < 2:
        return None, None
    latest = rows[0][0]
    base = rows[min(n_back, len(rows) - 1)][0]
    return latest, base


_rotation_cache_key: str | None = None
_rotation_cache_data: dict[str, Any] | None = None


def compute_sector_rotation_signals(*, window_days: int = 5, force: bool = False) -> dict[str, Any]:
    global _rotation_cache_key, _rotation_cache_data
    from services.market_data_cache import TTL_SECTOR_ROTATION_SEC

    latest = latest_trading_date()
    cache_key = f"{latest}:{window_days}"
    if (
        not force
        and _rotation_cache_key == cache_key
        and _rotation_cache_data is not None
    ):
        cached = _rotation_cache_data
        if cached.get("_cached_at", 0) + TTL_SECTOR_ROTATION_SEC > __import__("time").time():
            out = dict(cached)
            out.pop("_cached_at", None)
            return out

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    latest_dt, base_dt = _recent_trade_dates(conn, window_days)
    if not latest_dt or not base_dt:
        conn.close()
        return {"error": "行情交易日不足", "all": [], "add": [], "reduce": []}

    join_cs = per_stock_latest_join("cs")
    rows = conn.execute(
        f"""
        SELECT s.id AS stock_id, s.code, s.name, s.industry_sw,
               cs.composite_v5,
               q0.close AS close_now,
               q1.close AS close_base
        FROM stocks s
        {join_cs}
        LEFT JOIN stock_daily_quotes q0 ON q0.stock_id = s.id AND q0.trade_date = ?
        LEFT JOIN stock_daily_quotes q1 ON q1.stock_id = s.id AND q1.trade_date = ?
        WHERE s.is_active = 1 AND s.industry_sw IS NOT NULL
          AND q0.close IS NOT NULL AND q1.close IS NOT NULL AND q1.close > 0
        """,
        (latest_dt, base_dt),
    ).fetchall()
    conn.close()

    stock_returns: list[dict[str, Any]] = []
    for r in rows:
        ret = round((float(r["close_now"]) / float(r["close_base"]) - 1) * 100, 2)
        stock_returns.append(
            {
                "stock_id": r["stock_id"],
                "code": r["code"],
                "name": r["name"],
                "industry": r["industry_sw"],
                "return_5d": ret,
                "composite_v5": round(float(r["composite_v5"]), 1) if r["composite_v5"] is not None else None,
                "price": round(float(r["close_now"]), 2),
            }
        )

    if not stock_returns:
        return {
            "error": "无有效涨跌幅样本",
            "as_of_trade_date": latest_dt,
            "base_trade_date": base_dt,
            "all": [],
            "add": [],
            "reduce": [],
        }

    pool_avg = round(sum(s["return_5d"] for s in stock_returns) / len(stock_returns), 2)

    by_ind: dict[str, list[dict]] = defaultdict(list)
    for s in stock_returns:
        by_ind[s["industry"]].append(s)

    sectors: list[dict[str, Any]] = []
    for ind, items in by_ind.items():
        avg_ret = round(sum(x["return_5d"] for x in items) / len(items), 2)
        rel = round(avg_ret - pool_avg, 2)
        if rel >= 1.0:
            signal = "加仓"
        elif rel <= -1.0:
            signal = "减仓"
        else:
            signal = "持有"
        items_sorted = sorted(items, key=lambda x: -x["return_5d"])
        sectors.append(
            {
                "industry": ind,
                "avg_return_5d": avg_ret,
                "rel_strength": rel,
                "stock_count": len(items),
                "signal": signal,
                "stocks": items_sorted,
                # 兼容旧字段名
                "score": avg_ret,
                "momentum": rel,
            }
        )

    sectors.sort(key=lambda x: -x["rel_strength"])
    add = [s for s in sectors if s["signal"] == "加仓"][:5]
    reduce = sorted(
        [s for s in sectors if s["signal"] == "减仓"],
        key=lambda x: x["rel_strength"],
    )[:5]

    result = {
        "date": latest_trading_date(),
        "as_of_trade_date": latest_dt,
        "base_trade_date": base_dt,
        "window_trading_days": window_days,
        "pool_avg_return_5d": pool_avg,
        "method": f"近{window_days}个交易日涨跌幅；相对强度=行业均值-跟踪池均值",
        "signal_rule": "加仓:相对强度≥+1.0%；减仓:相对强度≤-1.0%",
        "add": add,
        "reduce": reduce,
        "all": sectors,
        "cached": False,
    }
    _rotation_cache_key = cache_key
    _rotation_cache_data = {**result, "_cached_at": __import__("time").time(), "cached": True}
    return result


def clear_sector_rotation_cache() -> None:
    global _rotation_cache_key, _rotation_cache_data
    _rotation_cache_key = None
    _rotation_cache_data = None
