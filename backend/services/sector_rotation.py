"""行业轮动 — 5 个交易日涨跌幅 + 相对跟踪池强度"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any

from config import DB_PATH, SECTOR_CROWDING_BLOCK, SECTOR_CROWDING_WARN, latest_trading_date
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


def _load_sector_fund_flow(conn: sqlite3.Connection) -> dict[str, float]:
    """行业名 -> 5日净流入占比（用于拥挤度）。"""
    try:
        row = conn.execute(
            "SELECT MAX(trade_date) FROM sector_fund_flow_daily",
        ).fetchone()
        if not row or not row[0]:
            return {}
        dt = row[0]
        rows = conn.execute(
            """SELECT sector_name, net_inflow_pct FROM sector_fund_flow_daily
               WHERE trade_date=?""",
            (dt,),
        ).fetchall()
        return {str(r[0]): float(r[1] or 0) for r in rows}
    except sqlite3.OperationalError:
        return {}


def _industry_turnover_ratio(conn: sqlite3.Connection, stock_ids: list[int], latest_dt: str) -> float:
    """行业近5日平均换手率 / 近60日平均（>2 视为交易拥挤）。"""
    if not stock_ids:
        return 1.0
    ph = ",".join("?" * len(stock_ids))
    rows = conn.execute(
        f"""SELECT turnover FROM stock_daily_quotes
            WHERE stock_id IN ({ph}) AND trade_date <= ? AND turnover IS NOT NULL
            ORDER BY trade_date DESC LIMIT ?""",
        (*stock_ids, latest_dt, len(stock_ids) * 60),
    ).fetchall()
    vals = [float(r[0]) for r in rows if r[0] is not None and float(r[0]) > 0]
    if len(vals) < 10:
        return 1.0
    short = vals[: min(len(vals), len(stock_ids) * 5)]
    long = vals[: min(len(vals), len(stock_ids) * 60)]
    avg_s = sum(short) / len(short) if short else 1.0
    avg_l = sum(long) / len(long) if long else 1.0
    return avg_s / avg_l if avg_l > 0 else 1.0


def _sector_crowding_score(
    rel_strength: float,
    max_rel: float,
    turnover_ratio: float,
    fund_flow_pct: float | None,
) -> float:
    """0–100，越高越拥挤。"""
    mom_part = min(100.0, max(0.0, (rel_strength / max_rel) * 50)) if max_rel > 0 else 0.0
    turn_part = min(40.0, max(0.0, (turnover_ratio - 1.0) * 20))
    flow_part = 0.0
    if fund_flow_pct is not None and fund_flow_pct > 0:
        flow_part = min(30.0, fund_flow_pct * 3)
    return round(min(100.0, mom_part + turn_part + flow_part), 1)


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
    fund_flow_map = _load_sector_fund_flow(conn)
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
    rel_strengths = []
    for ind, items in by_ind.items():
        avg_ret = round(sum(x["return_5d"] for x in items) / len(items), 2)
        rel = round(avg_ret - pool_avg, 2)
        rel_strengths.append(rel)
    max_rel = max(rel_strengths) if rel_strengths else 1.0

    crowding_warnings: list[dict[str, Any]] = []
    for ind, items in by_ind.items():
        avg_ret = round(sum(x["return_5d"] for x in items) / len(items), 2)
        rel = round(avg_ret - pool_avg, 2)
        sids = [int(x["stock_id"]) for x in items]
        turn_ratio = _industry_turnover_ratio(conn, sids, latest_dt)
        flow_pct = fund_flow_map.get(ind)
        crowding = _sector_crowding_score(rel, max_rel, turn_ratio, flow_pct)
        if rel >= 1.0:
            signal = "加仓"
        elif rel <= -1.0:
            signal = "减仓"
        else:
            signal = "持有"
        if signal == "加仓" and crowding >= SECTOR_CROWDING_BLOCK:
            signal = "拥挤"
            crowding_warnings.append(
                {"industry": ind, "rel_strength": rel, "crowding": crowding, "level": "block"}
            )
        elif signal == "加仓" and crowding >= SECTOR_CROWDING_WARN:
            crowding_warnings.append(
                {"industry": ind, "rel_strength": rel, "crowding": crowding, "level": "warn"}
            )
        items_sorted = sorted(items, key=lambda x: -x["return_5d"])
        sectors.append(
            {
                "industry": ind,
                "avg_return_5d": avg_ret,
                "rel_strength": rel,
                "crowding": crowding,
                "turnover_ratio": round(turn_ratio, 2),
                "fund_flow_pct": flow_pct,
                "stock_count": len(items),
                "signal": signal,
                "stocks": items_sorted,
                "score": avg_ret,
                "momentum": rel,
            }
        )

    conn.close()

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
        "method": f"近{window_days}个交易日涨跌幅；相对强度=行业均值-跟踪池均值；拥挤度=动量+换手+资金",
        "signal_rule": "加仓:相对强度≥+1.0%且拥挤<85；拥挤≥70预警",
        "crowding_warn_threshold": SECTOR_CROWDING_WARN,
        "crowding_block_threshold": SECTOR_CROWDING_BLOCK,
        "crowding_warnings": crowding_warnings,
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
