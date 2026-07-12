"""时序存储抽象 — 当前仅 SQLite；DolphinDB 远期实现"""
from __future__ import annotations

import sqlite3
from typing import List, Optional, Protocol

from config import DB_PATH, DB_READ_PATH, DATA_ENGINE


class TimeSeriesStore(Protocol):
    def read_bars(
        self,
        codes: Optional[List[str]] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        stock_id: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[dict]:
        ...


class SQLiteStore:
    """只读行情访问，优先读 snapshot 副本"""

    def __init__(self, db_path: Optional[str] = None):
        import os

        read = db_path or DB_READ_PATH
        self.path = read if os.path.exists(read) else DB_PATH

    def read_bars(
        self,
        codes: Optional[List[str]] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        stock_id: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[dict]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        q = """SELECT s.code, s.name, q.trade_date, q.open, q.high, q.low, q.close, q.volume, q.stock_id
               FROM stock_daily_quotes q JOIN stocks s ON q.stock_id = s.id
               WHERE q.close IS NOT NULL"""
        args: list = []
        if stock_id:
            q += " AND q.stock_id=?"
            args.append(stock_id)
        if codes:
            placeholders = ",".join("?" * len(codes))
            q += f" AND s.code IN ({placeholders})"
            args.extend(codes)
        if start:
            q += " AND q.trade_date >= ?"
            args.append(start)
        if end:
            q += " AND q.trade_date <= ?"
            args.append(end)
        q += " ORDER BY q.trade_date"
        if limit:
            q += " LIMIT ?"
            args.append(limit)
        rows = [dict(r) for r in conn.execute(q, args).fetchall()]
        conn.close()
        return rows


def get_timeseries_store() -> TimeSeriesStore:
    if DATA_ENGINE == "dolphindb":
        raise NotImplementedError("DolphinDB 尚未启用，请保持 AFR_DATA_ENGINE=sqlite")
    return SQLiteStore()
