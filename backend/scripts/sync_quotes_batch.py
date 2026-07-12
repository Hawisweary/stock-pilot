"""
批量并发日行情同步 — 替代串行 fetch_job（仅行情部分）
使用 akshare stock_zh_a_hist + ThreadPoolExecutor 并发抓取。

用法：
  cd backend
  # 增量（默认抓最近 30 天，已有数据的只补缺口）
  ../venv-quant/bin/python scripts/sync_quotes_batch.py

  # 全量历史（新股或指定天数）
  ../venv-quant/bin/python scripts/sync_quotes_batch.py --days 500

  # 指定并发数（默认 12）
  ../venv-quant/bin/python scripts/sync_quotes_batch.py --workers 20

  # 只同步指定股票
  ../venv-quant/bin/python scripts/sync_quotes_batch.py --codes 000001,600000
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from threading import Lock

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

_db_lock = Lock()


def _fetch_one(code: str, stock_id: int, start: str, end: str) -> tuple[str, int, str | None]:
    """抓取单只股票历史行情，返回 (code, rows_inserted, error)"""
    import akshare as ak
    try:
        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
            adjust="qfq",
        )
        if df is None or df.empty:
            return code, 0, None

        rows = []
        for _, r in df.iterrows():
            rows.append((
                stock_id,
                str(r["日期"])[:10],
                float(r["开盘"]) if r["开盘"] else None,
                float(r["最高"]) if r["最高"] else None,
                float(r["最低"]) if r["最低"] else None,
                float(r["收盘"]) if r["收盘"] else None,
                int(r["成交量"]) if r["成交量"] else None,
                float(r["成交额"]) if r["成交额"] else None,
                float(r["涨跌幅"]) if r["涨跌幅"] else None,
                float(r["换手率"]) if r["换手率"] else None,
            ))

        if not rows:
            return code, 0, None

        with _db_lock:
            conn = sqlite3.connect(config.DB_PATH, timeout=30)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=30000")
                conn.executemany(
                    """INSERT OR REPLACE INTO stock_daily_quotes
                       (stock_id, trade_date, open, high, low, close, volume, amount, change_pct, turnover)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    rows,
                )
                conn.commit()
            finally:
                conn.close()

        return code, len(rows), None
    except Exception as e:
        return code, 0, str(e)


def _get_last_quote_dates(conn: sqlite3.Connection, stock_ids: list[int]) -> dict[int, str]:
    if not stock_ids:
        return {}
    placeholders = ",".join("?" * len(stock_ids))
    rows = conn.execute(
        f"SELECT stock_id, MAX(trade_date) FROM stock_daily_quotes WHERE stock_id IN ({placeholders}) GROUP BY stock_id",
        stock_ids,
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30, help="抓取天数（默认30天增量）")
    parser.add_argument("--workers", type=int, default=12, help="并发线程数（默认12）")
    parser.add_argument("--codes", type=str, default="", help="指定股票代码，逗号分隔")
    parser.add_argument("--full", action="store_true", help="全量模式（500天历史）")
    args = parser.parse_args()

    if args.full:
        args.days = 500

    end_date = date.today().isoformat()
    default_start = (date.today() - timedelta(days=args.days)).isoformat()

    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row

    if args.codes:
        codes_filter = [c.strip().zfill(6) for c in args.codes.split(",")]
        stocks = conn.execute(
            f"SELECT id, code FROM stocks WHERE is_active=1 AND code IN ({','.join('?'*len(codes_filter))})",
            codes_filter,
        ).fetchall()
    else:
        stocks = conn.execute("SELECT id, code FROM stocks WHERE is_active=1").fetchall()

    stock_ids = [s["id"] for s in stocks]
    last_dates = _get_last_quote_dates(conn, stock_ids)
    conn.close()

    tasks = []
    for s in stocks:
        sid, code = s["id"], s["code"]
        last = last_dates.get(sid)
        if last and not args.full:
            # 增量：从上次有数据的后一天开始
            start = (date.fromisoformat(last) + timedelta(days=1)).isoformat()
            if start > end_date:
                continue  # 已是最新
        else:
            start = default_start
        tasks.append((code, sid, start, end_date))

    print(f"共 {len(stocks)} 只股票，需同步 {len(tasks)} 只，并发 {args.workers} 线程")
    if not tasks:
        print("所有股票行情已是最新，无需同步")
        return

    total_rows = 0
    errors = []
    done = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_fetch_one, code, sid, start, end): code
                   for code, sid, start, end in tasks}
        for fut in as_completed(futures):
            code, rows, err = fut.result()
            done += 1
            total_rows += rows
            if err:
                errors.append(f"{code}: {err}")
            if done % 100 == 0 or done == len(tasks):
                elapsed = time.time() - t0
                speed = done / elapsed
                eta = (len(tasks) - done) / speed if speed > 0 else 0
                print(f"  {done}/{len(tasks)}  行情行数={total_rows}  错误={len(errors)}  ETA={eta:.0f}s")

    elapsed = time.time() - t0
    print(f"\n完成：{done} 只，共写入 {total_rows} 行，耗时 {elapsed:.1f}s，错误 {len(errors)} 只")
    if errors[:10]:
        print("部分错误：", errors[:10])


if __name__ == "__main__":
    main()
