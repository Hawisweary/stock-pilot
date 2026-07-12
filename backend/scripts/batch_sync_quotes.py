"""批量补齐 stock_daily_quotes — 多线程并发，只刷新落后的股票

背景: 全市场 5292 只股票里，只有 ~1.5% 行情更新到最新交易日，72% 停留在 4 个
交易日前 —— 原因是 services/quote_sync.py::sync_active_stock_quotes() 是纯串行
for 循环，5292 只全量跑一遍太慢，实际从未跑完过。

用法:
    python scripts/batch_sync_quotes.py                # 只刷新落后 >=1 交易日的股票
    python scripts/batch_sync_quotes.py --all           # 全部重新拉（忽略本地新鲜度）
    python scripts/batch_sync_quotes.py --workers 8     # 指定并发线程数（默认4）
    python scripts/batch_sync_quotes.py --max-bars 60   # 每只股票拉多少根K线（默认60，够补几天缺口）
    python scripts/batch_sync_quotes.py --limit 200     # 只处理前N只（测试用）

注意: 默认并发 4 是刻意调低的 —— 单只股票内部会先后调用腾讯行情/东财除权/东财成交额
三次外部接口，中间的 UPDATE 语句在最后统一 commit 前会一直挂着未提交的写事务。
并发太高（比如16）时多个线程同时长时间持有写事务，会撞上 SQLite「database is
locked」，触发失败重试反而更慢。实测 4 并发比 16 并发快 3-4 倍且零报错。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

DB_PATH = config.DB_PATH
_print_lock = Lock()
_counter_lock = Lock()
_stats = {"ok": 0, "skip": 0, "fail": 0, "zero": 0, "total": 0}


def _log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def _inc(key: str) -> None:
    with _counter_lock:
        _stats[key] += 1


def _fetch_one(stock_id: int, code: str, market: str, max_bars: int) -> dict:
    """单股行情补齐 — 每个线程独立连接，避免共享 sqlite3.Connection 跨线程问题。"""
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
    try:
        from services.data_fetcher import DataFetcher

        fetcher = DataFetcher(conn)
        n = fetcher._fetch_daily_quotes(stock_id, code, market=market or "A", max_bars=max_bars)
        conn.commit()
        return {"stock_id": stock_id, "code": code, "rows": n, "error": None}
    except Exception as e:
        return {"stock_id": stock_id, "code": code, "rows": 0, "error": str(e)[:200]}
    finally:
        conn.close()


def _stale_stock_ids(conn: sqlite3.Connection, min_lag_days: int) -> set[int]:
    """本地最新行情比全库最新交易日落后 >= min_lag_days 的股票 id 集合。"""
    latest_overall = conn.execute(
        "SELECT MAX(trade_date) FROM stock_daily_quotes WHERE close IS NOT NULL"
    ).fetchone()[0]
    if not latest_overall:
        return set()
    rows = conn.execute(
        """
        SELECT s.id
        FROM stocks s
        LEFT JOIN (
            SELECT stock_id, MAX(trade_date) AS latest
            FROM stock_daily_quotes
            GROUP BY stock_id
        ) q ON q.stock_id = s.id
        WHERE s.is_active = 1
          AND (q.latest IS NULL OR q.latest < date(?, ?))
        """,
        (latest_overall, f"-{min_lag_days} days"),
    ).fetchall()
    return {r[0] for r in rows}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="忽略本地新鲜度，全部重新拉取")
    parser.add_argument(
        "--workers", type=int, default=4,
        help="并发线程数（默认4 —— 单只股票内部会跨多次网络请求持有未提交写事务，"
             "并发太高会导致 SQLite 写锁冲突，实测16并发反而比4并发慢）",
    )
    parser.add_argument("--max-bars", type=int, default=60, help="每只股票拉取根数（默认60）")
    parser.add_argument("--min-lag-days", type=int, default=1, help="落后多少个交易日才算需要补（默认1）")
    parser.add_argument("--limit", type=int, default=0, help="最多处理N只（0=全部，测试用）")
    parser.add_argument("--market", default="", help="只处理指定市场 A/SH/SZ/BJ")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    where = "WHERE is_active=1"
    params: list = []
    if args.market:
        where += " AND market=?"
        params.append(args.market.upper())
    stocks = conn.execute(
        f"SELECT id, code, COALESCE(market,'A') AS market FROM stocks {where} ORDER BY id",
        params,
    ).fetchall()

    if not args.all:
        stale_ids = _stale_stock_ids(conn, args.min_lag_days)
        stocks = [s for s in stocks if s[0] in stale_ids]

    conn.close()

    if args.limit:
        stocks = stocks[: args.limit]

    total = len(stocks)
    _stats["total"] = total
    print(f"待补齐: {total} 只  并发: {args.workers} 线程  max_bars={args.max_bars}")

    if total == 0:
        print("没有需要补齐的股票，全市场行情已是最新。")
        return

    t0 = time.perf_counter()
    done = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(_fetch_one, s[0], s[1], s[2], args.max_bars): s for s in stocks
        }
        for fut in as_completed(futures):
            done += 1
            s = futures[fut]
            try:
                r = fut.result()
                if r["error"]:
                    _inc("fail")
                elif r["rows"] > 0:
                    _inc("ok")
                else:
                    _inc("zero")
            except Exception as e:
                _inc("fail")
                r = {"code": s[1], "rows": 0, "error": str(e)[:200]}

            if done % 50 == 0 or done == total:
                elapsed = time.perf_counter() - t0
                rate = done / elapsed if elapsed > 0 else 0
                eta = (total - done) / rate if rate > 0 else 0
                _log(
                    f"[{done}/{total}] ok={_stats['ok']} zero={_stats['zero']} fail={_stats['fail']}"
                    f"  速度={rate:.1f}只/s  剩余≈{eta/60:.1f}min"
                )

    elapsed = time.perf_counter() - t0
    print(
        f"\n完成  耗时={elapsed/60:.1f}min"
        f"  ok={_stats['ok']}  zero={_stats['zero']}（无新数据）  fail={_stats['fail']}"
    )

    # 收尾: 打印补齐后的新鲜度分布，直观确认效果
    conn2 = sqlite3.connect(DB_PATH)
    rows = conn2.execute(
        """
        SELECT latest_date, COUNT(*) FROM (
            SELECT stock_id, MAX(trade_date) AS latest_date
            FROM stock_daily_quotes GROUP BY stock_id
        ) GROUP BY latest_date ORDER BY latest_date DESC LIMIT 10
        """
    ).fetchall()
    conn2.close()
    print("\n补齐后新鲜度分布（最新10个交易日）：")
    for d, cnt in rows:
        print(f"  {d}: {cnt} 只")


if __name__ == "__main__":
    main()
