"""统一 SQLite 连接工厂 — SEC-OPS P1-4

Phase A 覆盖写路径（portfolio_svc、job_queue）。
读路径（backtest_engine、screener 等）在 Phase B 迁移。

规则：
- 新代码禁止裸 sqlite3.connect(DB_PATH)，统一用 connect_db()
- write=True 时设 busy_timeout=5000（写等待）
- ro=True 时设 PRAGMA query_only=ON
"""
from __future__ import annotations

import sqlite3

from config import DB_PATH


def connect_db(*, write: bool = False, ro: bool = False) -> sqlite3.Connection:
    """返回已配置 WAL + busy_timeout 的 SQLite 连接。

    Args:
        write: 写操作时传 True，启用 busy_timeout=5000ms
        ro:    只读连接，设 PRAGMA query_only=ON
    """
    timeout = 30.0 if write else 5.0
    conn = sqlite3.connect(DB_PATH, timeout=timeout)
    conn.execute("PRAGMA journal_mode=WAL")
    if write:
        conn.execute("PRAGMA busy_timeout=5000")
    if ro:
        conn.execute("PRAGMA query_only=ON")
    conn.row_factory = sqlite3.Row
    return conn
