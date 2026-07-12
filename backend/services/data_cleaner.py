"""行情数据清洗 — 复权价、停牌标记"""
from __future__ import annotations

import sqlite3
from typing import Optional

from config import DB_PATH


def ensure_quote_columns(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(stock_daily_quotes)").fetchall()}
    if "adj_close" not in cols:
        conn.execute("ALTER TABLE stock_daily_quotes ADD COLUMN adj_close REAL")
    if "is_suspended" not in cols:
        conn.execute(
            "ALTER TABLE stock_daily_quotes ADD COLUMN is_suspended INTEGER DEFAULT 0"
        )


def backfill_adj_close(conn: Optional[sqlite3.Connection] = None) -> dict:
    """adj_close 缺失时用 close 填充（腾讯源已是前复权）。"""
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    ensure_quote_columns(conn)
    before = conn.execute(
        "SELECT COUNT(*) FROM stock_daily_quotes WHERE adj_close IS NULL AND close IS NOT NULL"
    ).fetchone()[0]
    conn.execute(
        """UPDATE stock_daily_quotes
           SET adj_close = close
           WHERE adj_close IS NULL AND close IS NOT NULL"""
    )
    conn.execute(
        """UPDATE stock_daily_quotes
           SET is_suspended = 1
           WHERE (volume IS NULL OR volume = 0) AND close IS NOT NULL"""
    )
    if own:
        conn.commit()
        conn.close()
    return {"adj_close_filled": before}


def price_expr(alias: str = "q") -> str:
    """SQL 表达式：优先 adj_close。"""
    return f"COALESCE({alias}.adj_close, {alias}.close)"
