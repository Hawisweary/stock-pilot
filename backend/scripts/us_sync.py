"""美股同步:股票池(Tiingo supported_tickers)+ 日线行情(yfinance 批量)。

- --universe-only   只刷股票池(变动少,可周度)
- --quotes-period P yfinance 批量拉行情,P 如 5d/1mo/6mo/1y(默认 5d)
- --chunk N         每批下载 N 只(默认 120)
- --limit N         只处理前 N 只(测试用)
独立 us_* 表,不混 A股/ETF。全市场约 8290 只,首轮 6mo 回填较慢(分块+限速)。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from services.us_market_adapter import fetch_us_quotes_bulk, fetch_us_universe


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS us_stocks (
        ticker TEXT PRIMARY KEY, name TEXT, exchange TEXT,
        start_date TEXT, end_date TEXT, is_active INTEGER DEFAULT 1,
        updated_at TEXT DEFAULT (datetime('now')))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS us_daily_quotes (
        ticker TEXT, trade_date TEXT, open REAL, high REAL, low REAL, close REAL,
        adj_close REAL, volume REAL, PRIMARY KEY (ticker, trade_date))""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_us_quotes_date ON us_daily_quotes(trade_date)")
    conn.commit()


def sync_universe(conn: sqlite3.Connection) -> int:
    rows = fetch_us_universe()
    if not rows:
        return 0
    # 先全部标记非活跃,再 upsert 活跃的(退市股自然 is_active=0)
    conn.execute("UPDATE us_stocks SET is_active=0")
    conn.executemany(
        """INSERT INTO us_stocks (ticker, exchange, start_date, end_date, is_active, updated_at)
           VALUES (:ticker, :exchange, :start_date, :end_date, 1, datetime('now'))
           ON CONFLICT(ticker) DO UPDATE SET
             exchange=excluded.exchange, start_date=excluded.start_date,
             end_date=excluded.end_date, is_active=1, updated_at=datetime('now')""",
        rows,
    )
    conn.commit()
    return len(rows)


def sync_quotes(conn: sqlite3.Connection, period: str, chunk: int, limit: int | None) -> int:
    tickers = [r[0] for r in conn.execute("SELECT ticker FROM us_stocks WHERE is_active=1 ORDER BY ticker")]
    if limit:
        tickers = tickers[:limit]
    total = 0
    n_chunks = (len(tickers) + chunk - 1) // chunk
    for i in range(0, len(tickers), chunk):
        batch = tickers[i:i + chunk]
        try:
            rows = fetch_us_quotes_bulk(batch, period=period)
        except Exception as e:
            print(f"  批 {i//chunk+1}/{n_chunks} 失败: {str(e)[:60]}")
            continue
        if rows:
            conn.executemany(
                """INSERT OR REPLACE INTO us_daily_quotes
                   (ticker, trade_date, open, high, low, close, adj_close, volume)
                   VALUES (:ticker,:trade_date,:open,:high,:low,:close,:adj_close,:volume)""",
                rows,
            )
            conn.commit()
            total += len(rows)
        print(f"  批 {i//chunk+1}/{n_chunks}: {len(batch)}只 → {len(rows) if rows else 0}行 (累计{total})")
        if (i // chunk) % 3 == 2:
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.OperationalError:
                pass
        time.sleep(0.5)  # 轻限速,避免 Yahoo 限流
    return total


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe-only", action="store_true")
    ap.add_argument("--quotes-period", default="5d")
    ap.add_argument("--chunk", type=int, default=120)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    conn = sqlite3.connect(config.US_DB_PATH, timeout=60)  # 独立库,不碰主库锁
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        _ensure_tables(conn)
        n = sync_universe(conn)
        print(f"[美股池] 活跃 {n} 只")
        if not args.universe_only:
            q = sync_quotes(conn, args.quotes_period, args.chunk, args.limit)
            print(f"[美股行情] period={args.quotes_period} 共写入 {q} 行")
    finally:
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.OperationalError:
            pass
        conn.close()


if __name__ == "__main__":
    main()
