"""Polars 数据桥 — 统一从 snapshot 只读库加载 Arrow/Polars"""
from __future__ import annotations

from typing import Optional

from config import USE_POLARS
from services.timeseries_store import get_timeseries_store


def read_quotes_polars(stock_id: int = None, days: int = 365, code: str = None):
    """加载日线 → Polars DataFrame；未安装 polars 或未开 Flag 时返回 None"""
    if not USE_POLARS:
        return None
    try:
        import polars as pl
    except ImportError:
        return None

    store = get_timeseries_store()
    if stock_id:
        rows = store.read_bars(stock_id=stock_id, limit=days)
        if not rows:
            return pl.DataFrame()
        return (
            pl.DataFrame(rows)
            .select(["trade_date", "open", "high", "low", "close", "volume"])
            .rename({"trade_date": "date"})
            .sort("date")
        )
    codes = [code] if code else None
    rows = store.read_bars(codes=codes, limit=days * 50)
    if not rows:
        return pl.DataFrame()
    return (
        pl.DataFrame(rows)
        .select(["code", "trade_date", "open", "high", "low", "close", "volume"])
        .rename({"trade_date": "date"})
        .sort("date")
    )


def polars_available() -> bool:
    if not USE_POLARS:
        return False
    try:
        import polars  # noqa: F401
        return True
    except ImportError:
        return False
