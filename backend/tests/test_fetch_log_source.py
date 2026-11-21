"""data_fetch_log.source 列迁移与写入"""
import sqlite3


def test_fetch_log_source_column():
    from migrations import _safe_add_column

    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE data_fetch_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER, data_type TEXT, status TEXT
        )"""
    )
    _safe_add_column(conn, "data_fetch_log", "source", "source TEXT DEFAULT ''")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(data_fetch_log)").fetchall()}
    assert "source" in cols


def test_data_fetcher_log_writes_source(tmp_path, redirect_db):
    import sqlite3
    from database import cache_connect
    from services.data_fetcher import DataFetcher

    # _log 写的是缓存库(cache_connect),不是传入的 conn;重定向到临时库,别碰真实 cache.db
    redirect_db(tmp_path / "afr.db", tmp_path / "cache.db")

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE data_fetch_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, stock_id INTEGER, data_type TEXT,
            fetch_time TEXT DEFAULT (datetime('now')), status TEXT, records_count INTEGER,
            error_message TEXT, duration_ms INTEGER, source TEXT DEFAULT ''
        )"""
    )
    conn.commit()
    DataFetcher(conn)._log(1, "quotes", "success", 10, source="tencent")
    cconn = cache_connect()
    try:
        row = cconn.execute("SELECT source FROM data_fetch_log").fetchone()
    finally:
        cconn.close()
    assert row["source"] == "tencent"
