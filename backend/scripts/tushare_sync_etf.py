"""同步场内 ETF 列表 + 日线行情到本地 etfs / etf_daily_quotes 表。

- --list-only     只刷 ETF 列表（fund_basic，变动少，可周度）
- --quotes-days N 回补最近 N 个交易日日线（默认 5）
独立于个股管线：ETF 自成两张表，不混入 stocks，不参与 V5 评分/因子。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from services.tushare_adapter import fetch_etf_list, fetch_etf_quotes


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS etfs (
        ts_code TEXT PRIMARY KEY, code TEXT, name TEXT, management TEXT,
        fund_type TEXT, invest_type TEXT, list_date TEXT,
        is_active INTEGER DEFAULT 1, updated_at TEXT DEFAULT (datetime('now')))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS etf_daily_quotes (
        ts_code TEXT, trade_date TEXT, open REAL, high REAL, low REAL, close REAL,
        pre_close REAL, change REAL, pct_chg REAL, vol REAL, amount REAL,
        PRIMARY KEY (ts_code, trade_date))""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_etf_quotes_date ON etf_daily_quotes(trade_date)")
    conn.commit()


def _trading_days(days: int) -> list[str]:
    from services.tushare_adapter import _pro, _throttle

    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=int(days * 1.6) + 10)).strftime("%Y%m%d")
    pro = _pro()
    _throttle()
    df = pro.trade_cal(exchange="SSE", start_date=start, end_date=end, is_open="1")
    if df is None or df.empty:
        return []
    return sorted(df["cal_date"].astype(str).tolist())[-days:]


def sync_etf_list(conn: sqlite3.Connection) -> int:
    rows = fetch_etf_list()
    if not rows:
        return 0
    conn.executemany(
        """INSERT OR REPLACE INTO etfs
           (ts_code, code, name, management, fund_type, invest_type, list_date, is_active, updated_at)
           VALUES (:ts_code, :code, :name, :management, :fund_type, :invest_type, :list_date, 1, datetime('now'))""",
        rows,
    )
    conn.commit()
    return len(rows)


def sync_etf_quotes(conn: sqlite3.Connection, days: int) -> int:
    # 只保留 etfs 表里的 ts_code（fund_daily 按日返回全部场内基金，含非 ETF）
    etf_codes = {r[0] for r in conn.execute("SELECT ts_code FROM etfs WHERE is_active=1")}
    if not etf_codes:
        return 0
    total = 0
    for i, d in enumerate(_trading_days(days), 1):
        trade_date_fmt = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        db_rows = [
            (r["ts_code"], trade_date_fmt, r["open"], r["high"], r["low"], r["close"],
             r["pre_close"], r["change"], r["pct_chg"], r["vol"], r["amount"])
            for r in fetch_etf_quotes(d) if r["ts_code"] in etf_codes
        ]
        if db_rows:
            conn.executemany(
                """INSERT OR REPLACE INTO etf_daily_quotes
                   (ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                db_rows,
            )
            conn.commit()
            total += len(db_rows)
        # 每几个交易日主动 checkpoint，别让 WAL 堆起来（复用锁治理经验）
        if i % 3 == 0:
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.OperationalError:
                pass
    return total


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list-only", action="store_true", help="只刷 ETF 列表")
    parser.add_argument("--quotes-days", type=int, default=5, help="回补最近 N 个交易日日线")
    args = parser.parse_args()

    conn = sqlite3.connect(config.DB_PATH, timeout=60)
    try:
        _ensure_tables(conn)
        n_list = sync_etf_list(conn)
        print(f"[ETF列表] 共写入 {n_list} 只")
        if not args.list_only:
            n_q = sync_etf_quotes(conn, args.quotes_days)
            print(f"[ETF行情] 近{args.quotes_days}交易日共写入 {n_q} 条")
    finally:
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.OperationalError:
            pass
        conn.close()


if __name__ == "__main__":
    main()
