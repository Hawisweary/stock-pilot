"""基本面百分位基准缓存 — 5000 股增量计算加速"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Optional

from config import DB_PATH

_cache: dict[str, dict] = {}


def cache_key(calc_date: str, benchmark_mode: str) -> str:
    return f"{calc_date}:{benchmark_mode}"


def get_universe_metrics(calc_date: str, conn: sqlite3.Connection, *, force_refresh: bool = False) -> dict:
    """当日全市场 metrics 缓存（内存 + DB 元数据）。"""
    from services.factor_engine import FactorEngine

    fe = FactorEngine(conn)
    key = cache_key(calc_date, fe.benchmark_mode)
    if not force_refresh and key in _cache:
        return _cache[key]

    ensure_cache_table(conn)
    if not force_refresh:
        row = conn.execute(
            "SELECT stock_count FROM factor_metrics_cache WHERE cache_key=?",
            (key,),
        ).fetchone()
        if row and key in _cache:
            return _cache[key]

    ids = fe._active_stock_ids()
    metrics = fe._get_all_metrics(ids)
    stocks_info = fe._load_stocks_info(ids)
    _cache[key] = {"metrics": metrics, "stocks_info": stocks_info, "universe_ids": ids}
    conn.execute(
        """INSERT OR REPLACE INTO factor_metrics_cache
           (cache_key, calc_date, benchmark_mode, stock_count, updated_at)
           VALUES (?, ?, ?, ?, datetime('now'))""",
        (key, calc_date, fe.benchmark_mode, len(ids)),
    )
    conn.commit()
    return _cache[key]


def invalidate(calc_date: Optional[str] = None) -> None:
    global _cache
    if calc_date is None:
        _cache = {}
        return
    _cache = {k: v for k, v in _cache.items() if not k.startswith(f"{calc_date}:")}


def refresh_daily_baseline(conn: Optional[sqlite3.Connection] = None) -> dict:
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
    calc_date = datetime.now().strftime("%Y-%m-%d")
    invalidate(calc_date)
    data = get_universe_metrics(calc_date, conn, force_refresh=True)
    if own:
        conn.close()
    return {"calc_date": calc_date, "stock_count": len(data.get("universe_ids", []))}


def ensure_cache_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS factor_metrics_cache (
            cache_key TEXT PRIMARY KEY,
            calc_date TEXT NOT NULL,
            benchmark_mode TEXT NOT NULL,
            stock_count INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
