"""股票生命周期 — 幸存者偏差校正（历史截面按上市/退市日过滤）"""
from __future__ import annotations

import sqlite3
from datetime import date
from typing import Dict, Optional

from config import DB_PATH

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS stock_lifecycle (
    stock_id INTEGER PRIMARY KEY,
    code TEXT NOT NULL,
    list_date TEXT,
    delist_date TEXT,
    source TEXT DEFAULT 'stocks',
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_lifecycle_list ON stock_lifecycle(list_date);
CREATE INDEX IF NOT EXISTS idx_lifecycle_delist ON stock_lifecycle(delist_date);
"""


def ensure_tables(conn: Optional[sqlite3.Connection] = None) -> None:
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    conn.executescript(CREATE_SQL)
    conn.commit()
    if own:
        conn.close()


def sync_lifecycle_from_stocks(conn: Optional[sqlite3.Connection] = None) -> dict:
    """从 stocks + 最后交易日推断退市日，写入 stock_lifecycle。"""
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    ensure_tables(conn)

    rows = conn.execute(
        """SELECT s.id, s.code, s.list_date, s.is_active,
                  (SELECT MAX(trade_date) FROM stock_daily_quotes q WHERE q.stock_id = s.id) AS last_quote
           FROM stocks s"""
    ).fetchall()

    upserted = 0
    for sid, code, list_date, is_active, last_quote in rows:
        list_d = (list_date or "").strip() or None
        delist_d = None
        if not is_active and last_quote:
            delist_d = last_quote
        conn.execute(
            """INSERT INTO stock_lifecycle (stock_id, code, list_date, delist_date, source, updated_at)
               VALUES (?, ?, ?, ?, 'stocks', datetime('now'))
               ON CONFLICT(stock_id) DO UPDATE SET
                 code=excluded.code,
                 list_date=COALESCE(excluded.list_date, stock_lifecycle.list_date),
                 delist_date=COALESCE(excluded.delist_date, stock_lifecycle.delist_date),
                 updated_at=datetime('now')""",
            (sid, code, list_d, delist_d),
        )
        upserted += 1

    if own:
        conn.commit()
        conn.close()
    return {"upserted": upserted}


def import_delisted_from_akshare(conn: Optional[sqlite3.Connection] = None) -> dict:
    """可选：从 AKShare 补充退市股生命周期（网络不可用时静默跳过）。"""
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    ensure_tables(conn)

    imported = 0
    try:
        import akshare as ak

        df = ak.stock_info_a_code_name()
        if df is None or df.empty:
            raise RuntimeError("empty akshare response")
        # MVP：仅标记当前库内 inactive 股；全量退市列表需专用接口
        _ = df
    except Exception:
        if own:
            conn.close()
        return {"imported": 0, "skipped": True, "reason": "akshare_unavailable"}

    if own:
        conn.commit()
        conn.close()
    return {"imported": imported, "skipped": False}


def _load_lifecycle_map(conn: sqlite3.Connection) -> Dict[int, tuple[Optional[str], Optional[str]]]:
    ensure_tables(conn)
    sync_lifecycle_from_stocks(conn)
    rows = conn.execute(
        "SELECT stock_id, list_date, delist_date FROM stock_lifecycle"
    ).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def is_alive(stock_id: int, as_of_date: str, lifecycle: Optional[Dict[int, tuple]] = None) -> bool:
    """as_of_date 当日是否已上市且未退市。"""
    if lifecycle is None:
        conn = sqlite3.connect(DB_PATH)
        lifecycle = _load_lifecycle_map(conn)
        conn.close()

    list_d, delist_d = lifecycle.get(stock_id, (None, None))
    if list_d and as_of_date < list_d:
        return False
    if delist_d and as_of_date > delist_d:
        return False
    return True


def alive_stock_ids(as_of_date: str, conn: Optional[sqlite3.Connection] = None) -> set[int]:
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    lifecycle = _load_lifecycle_map(conn)
    ids = {sid for sid in lifecycle if is_alive(sid, as_of_date, lifecycle)}
    if not ids:
        rows = conn.execute("SELECT id FROM stocks").fetchall()
        ids = {r[0] for r in rows}
    if own:
        conn.close()
    return ids


def lifecycle_stats(conn: Optional[sqlite3.Connection] = None) -> dict:
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    ensure_tables(conn)
    sync_lifecycle_from_stocks(conn)
    total = conn.execute("SELECT COUNT(*) FROM stock_lifecycle").fetchone()[0]
    delisted = conn.execute(
        "SELECT COUNT(*) FROM stock_lifecycle WHERE delist_date IS NOT NULL"
    ).fetchone()[0]
    if own:
        conn.close()
    return {"total": total, "delisted_tracked": delisted}
