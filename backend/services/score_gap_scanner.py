"""comprehensive_scores 维度缺口扫描"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

import config
from services.sentiment_aggregate import batch_get_sentiment_scores

REQUIRED_DIMENSIONS = config.SYNC_REQUIRED_DIMENSIONS or [
    "fundamental_score",
    "capital_score",
    "policy_score",
    "mood_score",
    "val_score",
]

ALL_SYNC_DIMENSIONS = [
    "fundamental_score",
    "technical_score",
    "sentiment_score",
    "capital_score",
    "policy_score",
    "mood_score",
    "val_score",
]

DIMENSION_SPEC: dict[str, tuple[str, str, str]] = {
    "fundamental_score": ("factor_scores", "composite_score", "calc_date"),
    "technical_score": ("tech_analysis_cache", "score", "created_at"),
    "capital_score": ("capital_scores", "composite_score", "date"),
    "policy_score": ("policy_scores", "composite_score", "date"),
    "mood_score": ("sentiment_scores", "composite_score", "date"),
    "val_score": ("valuation_scores", "composite_score", "date"),
}

_DATETIME_DIMS = {"technical_score"}


def is_source_stale(
    source_date: str | None,
    target_date: str,
    stale_days: int | None = None,
) -> bool:
    """源表日期早于 target_date - stale_days 则视为 stale。"""
    if not source_date:
        return False
    n = config.GAP_STALE_DAYS if stale_days is None else stale_days
    if n <= 0:
        return False
    try:
        src = datetime.strptime(str(source_date)[:10], "%Y-%m-%d")
        target = datetime.strptime(str(target_date)[:10], "%Y-%m-%d")
        return (target - src).days > n
    except ValueError:
        return False


def _normalize_date(raw: str | None, *, use_datetime: bool = False) -> str | None:
    if not raw:
        return None
    return str(raw).split(" ")[0] if use_datetime else str(raw)[:10]

DIMENSION_SPEC: dict[str, tuple[str, str, str]] = {
    "fundamental_score": ("factor_scores", "composite_score", "calc_date"),
    "technical_score": ("tech_analysis_cache", "score", "created_at"),
    "capital_score": ("capital_scores", "composite_score", "date"),
    "policy_score": ("policy_scores", "composite_score", "date"),
    "mood_score": ("sentiment_scores", "composite_score", "date"),
    "val_score": ("valuation_scores", "composite_score", "date"),
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _active_stock_ids(conn: sqlite3.Connection, stock_ids: list[int] | None) -> list[int]:
    if stock_ids:
        return stock_ids
    rows = conn.execute("SELECT id FROM stocks WHERE is_active=1 ORDER BY id").fetchall()
    return [int(r["id"]) for r in rows]


def _latest_map(conn: sqlite3.Connection, sql: str, stock_ids: list[int]) -> dict[int, float]:
    if not stock_ids:
        return {}
    placeholders = ",".join(["?"] * len(stock_ids))
    rows = conn.execute(sql.format(ph=placeholders), tuple(stock_ids)).fetchall()
    return {int(r[0]): float(r[1]) for r in rows if r[1] is not None}


def _batch_source_maps(conn: sqlite3.Connection, stock_ids: list[int]) -> dict[str, dict[int, float]]:
    maps: dict[str, dict[int, float]] = {}
    table_sql = {
        "fundamental_score": """
            SELECT fs.stock_id, fs.composite_score
            FROM factor_scores fs
            INNER JOIN (
                SELECT stock_id, MAX(calc_date) AS md FROM factor_scores
                WHERE stock_id IN ({ph}) GROUP BY stock_id
            ) t ON fs.stock_id = t.stock_id AND fs.calc_date = t.md
        """,
        "technical_score": """
            SELECT tc.stock_id, tc.score
            FROM tech_analysis_cache tc
            INNER JOIN (
                SELECT stock_id, MAX(created_at) AS md FROM tech_analysis_cache
                WHERE stock_id IN ({ph}) GROUP BY stock_id
            ) t ON tc.stock_id = t.stock_id AND tc.created_at = t.md
        """,
        "capital_score": """
            SELECT cs.stock_id, cs.composite_score
            FROM capital_scores cs
            INNER JOIN (
                SELECT stock_id, MAX(date) AS md FROM capital_scores
                WHERE stock_id IN ({ph}) GROUP BY stock_id
            ) t ON cs.stock_id = t.stock_id AND cs.date = t.md
        """,
        "policy_score": """
            SELECT ps.stock_id, ps.composite_score
            FROM policy_scores ps
            INNER JOIN (
                SELECT stock_id, MAX(date) AS md FROM policy_scores
                WHERE stock_id IN ({ph}) GROUP BY stock_id
            ) t ON ps.stock_id = t.stock_id AND ps.date = t.md
        """,
        "mood_score": """
            SELECT ss.stock_id, ss.composite_score
            FROM sentiment_scores ss
            INNER JOIN (
                SELECT stock_id, MAX(date) AS md FROM sentiment_scores
                WHERE stock_id IN ({ph}) GROUP BY stock_id
            ) t ON ss.stock_id = t.stock_id AND ss.date = t.md
        """,
        "val_score": """
            SELECT vs.stock_id, vs.composite_score
            FROM valuation_scores vs
            INNER JOIN (
                SELECT stock_id, MAX(date) AS md FROM valuation_scores
                WHERE stock_id IN ({ph}) GROUP BY stock_id
            ) t ON vs.stock_id = t.stock_id AND vs.date = t.md
        """,
    }
    for dim, sql in table_sql.items():
        maps[dim] = _latest_map(conn, sql, stock_ids)
    return maps


def _batch_source_date_maps(conn: sqlite3.Connection, stock_ids: list[int]) -> dict[str, dict[int, str]]:
    if not stock_ids:
        return {}
    placeholders = ",".join(["?"] * len(stock_ids))
    date_sql = {
        "fundamental_score": f"""
            SELECT stock_id, MAX(calc_date) AS d FROM factor_scores
            WHERE stock_id IN ({placeholders}) GROUP BY stock_id
        """,
        "technical_score": f"""
            SELECT stock_id, MAX(created_at) AS d FROM tech_analysis_cache
            WHERE stock_id IN ({placeholders}) GROUP BY stock_id
        """,
        "capital_score": f"""
            SELECT stock_id, MAX(date) AS d FROM capital_scores
            WHERE stock_id IN ({placeholders}) GROUP BY stock_id
        """,
        "policy_score": f"""
            SELECT stock_id, MAX(date) AS d FROM policy_scores
            WHERE stock_id IN ({placeholders}) GROUP BY stock_id
        """,
        "mood_score": f"""
            SELECT stock_id, MAX(date) AS d FROM sentiment_scores
            WHERE stock_id IN ({placeholders}) GROUP BY stock_id
        """,
        "val_score": f"""
            SELECT stock_id, MAX(date) AS d FROM valuation_scores
            WHERE stock_id IN ({placeholders}) GROUP BY stock_id
        """,
    }
    out: dict[str, dict[int, str]] = {dim: {} for dim in ALL_SYNC_DIMENSIONS}
    for dim, sql in date_sql.items():
        rows = conn.execute(sql, tuple(stock_ids)).fetchall()
        use_dt = dim in _DATETIME_DIMS
        for r in rows:
            d = _normalize_date(r["d"], use_datetime=use_dt)
            if d:
                out[dim][int(r["stock_id"])] = d

    rows = conn.execute(
        f"""
        SELECT stock_id, MAX(pub_date) AS d FROM stock_news
        WHERE stock_id IN ({placeholders}) AND sentiment_score IS NOT NULL
        GROUP BY stock_id
        """,
        tuple(stock_ids),
    ).fetchall()
    for r in rows:
        d = _normalize_date(r["d"])
        if d:
            out["sentiment_score"][int(r["stock_id"])] = d
    return out


def _load_comprehensive(
    conn: sqlite3.Connection, stock_ids: list[int], target_date: str
) -> dict[int, dict[str, Any]]:
    """按每只股票最新 comprehensive 行扫描缺口（非全局 target_date 单行）。"""
    from services.comprehensive_store import load_latest_comprehensive_rows

    return load_latest_comprehensive_rows(conn, stock_ids)


def _dim_summary() -> dict[str, dict[str, int]]:
    return {dim: {"ok": 0, "missing": 0, "no_source": 0, "stale": 0} for dim in ALL_SYNC_DIMENSIONS}


def scan_gaps(
    target_date: str | None = None,
    stock_ids: list[int] | None = None,
    *,
    dimensions: list[str] | None = None,
) -> dict[str, Any]:
    """扫描 comprehensive 维度缺口，返回 sync_rate 与明细。"""
    conn = _connect()
    try:
        target = target_date or config.latest_trading_date()
        ids = _active_stock_ids(conn, stock_ids)
        dims = dimensions or ALL_SYNC_DIMENSIONS
        dims = [d for d in dims if d in ALL_SYNC_DIMENSIONS]

        comp_rows = _load_comprehensive(conn, ids, target)
        source_maps = _batch_source_maps(conn, ids)
        source_dates = _batch_source_date_maps(conn, ids)
        sentiment_map = batch_get_sentiment_scores(conn, ids, target)
        source_maps["sentiment_score"] = sentiment_map

        summary = _dim_summary()
        gaps: list[dict[str, Any]] = []
        missing_total = 0
        stale_total = 0

        stocks_all_ok = 0
        stocks_required_ok = 0

        for sid in ids:
            row = comp_rows.get(sid)
            stock_all_ok = True
            stock_required_ok = True

            for dim in dims:
                comp_val = row[dim] if row else None
                source_val = source_maps.get(dim, {}).get(sid)
                source_date = source_dates.get(dim, {}).get(sid)

                if comp_val is not None:
                    if is_source_stale(source_date, target):
                        summary[dim]["stale"] += 1
                        stale_total += 1
                        if dim in REQUIRED_DIMENSIONS:
                            stock_required_ok = False
                        stock_all_ok = False
                        gaps.append(
                            {
                                "stock_id": sid,
                                "dimension": dim,
                                "status": "stale",
                                "source_date": source_date,
                                "comp_value": comp_val,
                            }
                        )
                    else:
                        summary[dim]["ok"] += 1
                    continue

                if dim in ALL_SYNC_DIMENSIONS:
                    if dim in REQUIRED_DIMENSIONS:
                        stock_required_ok = False
                    stock_all_ok = False

                if source_val is not None:
                    summary[dim]["missing"] += 1
                    missing_total += 1
                    gaps.append(
                        {
                            "stock_id": sid,
                            "dimension": dim,
                            "status": "missing",
                            "source_value": source_val,
                            "source_date": source_date,
                        }
                    )
                else:
                    summary[dim]["no_source"] += 1
                    gaps.append(
                        {
                            "stock_id": sid,
                            "dimension": dim,
                            "status": "no_source",
                        }
                    )

            if stock_all_ok:
                stocks_all_ok += 1
            if stock_required_ok:
                stocks_required_ok += 1

        total = len(ids) or 1
        recommended: list[str] = []
        if missing_total > 0:
            recommended.append("sync_only")
        if stale_total > 0 and "sync_only" not in recommended:
            recommended.append("sync_only")
        if stale_total > 0:
            recommended.append("compute_and_sync")

        return {
            "target_date": target,
            "active_stocks_count": len(ids),
            "sync_rate_all": round(stocks_all_ok / total, 4),
            "sync_rate_required": round(stocks_required_ok / total, 4),
            "summary": summary,
            "gaps": gaps,
            "missing_total": missing_total,
            "stale_total": stale_total,
            "gap_stale_days": config.GAP_STALE_DAYS,
            "recommended_actions": recommended,
        }
    finally:
        conn.close()
