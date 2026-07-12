"""监视 batch_sync_quotes.py 的补齐进度 — 独立进程，轮询数据库

用法（新开一个终端，与 batch_sync_quotes.py 同时跑）:
    python scripts/monitor_quote_sync.py
    python scripts/monitor_quote_sync.py --interval 5   # 轮询间隔秒数（默认3）
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

DB_PATH = config.DB_PATH


def _snapshot() -> dict:
    conn = sqlite3.connect(DB_PATH)
    try:
        total_active = conn.execute("SELECT COUNT(*) FROM stocks WHERE is_active=1").fetchone()[0]
        latest_overall = conn.execute(
            "SELECT MAX(trade_date) FROM stock_daily_quotes WHERE close IS NOT NULL"
        ).fetchone()[0]
        fresh = conn.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT stock_id, MAX(trade_date) AS latest
                FROM stock_daily_quotes GROUP BY stock_id
            ) q
            JOIN stocks s ON s.id = q.stock_id
            WHERE s.is_active = 1 AND q.latest = ?
            """,
            (latest_overall,),
        ).fetchone()[0]
        dist = conn.execute(
            """
            SELECT latest_date, COUNT(*) FROM (
                SELECT q.stock_id, MAX(q.trade_date) AS latest_date
                FROM stock_daily_quotes q
                JOIN stocks s ON s.id = q.stock_id
                WHERE s.is_active = 1
                GROUP BY q.stock_id
            ) GROUP BY latest_date ORDER BY latest_date DESC LIMIT 5
            """
        ).fetchall()
        total_rows = conn.execute("SELECT COUNT(*) FROM stock_daily_quotes").fetchone()[0]
        return {
            "total_active": total_active,
            "latest_overall": latest_overall,
            "fresh": fresh,
            "dist": dist,
            "total_rows": total_rows,
        }
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=float, default=3.0, help="轮询间隔秒数（默认3）")
    args = parser.parse_args()

    print("监视行情补齐进度（Ctrl+C 退出）...\n")
    prev_fresh = -1
    prev_rows = -1
    t0 = time.perf_counter()

    try:
        while True:
            snap = _snapshot()
            pct = snap["fresh"] / snap["total_active"] * 100 if snap["total_active"] else 0
            row_delta = snap["total_rows"] - prev_rows if prev_rows >= 0 else 0
            elapsed = time.perf_counter() - t0

            if snap["fresh"] != prev_fresh or row_delta != 0:
                bar_len = 40
                filled = int(bar_len * pct / 100)
                bar = "#" * filled + "-" * (bar_len - filled)
                print(
                    f"[{elapsed:6.0f}s] [{bar}] {pct:5.1f}%"
                    f"  最新({snap['latest_overall']}): {snap['fresh']}/{snap['total_active']}"
                    f"  总行数={snap['total_rows']}"
                    f"  (+{row_delta if row_delta > 0 else 0}行/{args.interval:.0f}s)",
                    flush=True,
                )
                prev_fresh = snap["fresh"]
                prev_rows = snap["total_rows"]

            if pct >= 99.5:
                print("\n已基本补齐（>=99.5%覆盖最新交易日），可以停止监视了。")
                print("\n新鲜度分布（前5个交易日）：")
                for d, cnt in snap["dist"]:
                    print(f"  {d}: {cnt} 只")
                break

            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n已停止监视。")


if __name__ == "__main__":
    main()
