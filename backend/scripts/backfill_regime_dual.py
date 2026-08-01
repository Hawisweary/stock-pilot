#!/usr/bin/env python3
"""回填 market_regime_daily 双轨（CSI300 + CSI800）历史标签。

用法:
  cd backend && python scripts/backfill_regime_dual.py
  cd backend && python scripts/backfill_regime_dual.py --days 365
  cd backend && python scripts/backfill_regime_dual.py --from 2025-01-01 --to 2026-07-25
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from migrations import run_migrations
from services.market_regime import get_regime_agreement_stats, recompute_regime_persistence, sync_regime


def _trade_dates(conn: sqlite3.Connection, from_d: str, to_d: str) -> list[str]:
    rows = conn.execute(
        """SELECT DISTINCT trade_date FROM stock_daily_quotes
           WHERE trade_date >= ? AND trade_date <= ? AND close IS NOT NULL
           ORDER BY trade_date""",
        (from_d, to_d),
    ).fetchall()
    return [r[0] for r in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description="回填双轨市场状态")
    parser.add_argument("--days", type=int, default=365, help="最近 N 个交易日")
    parser.add_argument("--from", dest="from_date", help="起始日期 YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", help="结束日期 YYYY-MM-DD")
    parser.add_argument(
        "--persistence-days",
        type=int,
        default=None,
        help="状态确认最短持续天数（默认 REGIME_PERSISTENCE_DAYS）",
    )
    args = parser.parse_args()

    conn = sqlite3.connect(config.DB_PATH, timeout=60)
    run_migrations(conn)

    if args.from_date and args.to_date:
        dates = _trade_dates(conn, args.from_date, args.to_date)
    else:
        to_d = date.today().isoformat()
        from_d = (date.today() - timedelta(days=int(args.days) * 2)).isoformat()
        dates = _trade_dates(conn, from_d, to_d)[-args.days :]

    ok = err = 0
    for i, d in enumerate(dates, 1):
        try:
            r = sync_regime(conn, trade_date=d, apply_persistence=False)
            if r.get("error") and not r.get("trade_date"):
                err += 1
                print(f"[{i}/{len(dates)}] {d} FAIL: {r.get('error')}")
            else:
                ok += 1
                agree = "✓" if r.get("regime_label_agreement") else "✗"
                print(
                    f"[{i}/{len(dates)}] {d} "
                    f"300={r.get('regime_csi300_label', r.get('regime_label'))} "
                    f"800={r.get('regime_csi800_label')} {agree}"
                )
        except Exception as e:
            err += 1
            print(f"[{i}/{len(dates)}] {d} ERROR: {e}")

    stats = get_regime_agreement_stats(conn, days=len(dates))
    print(f"\n完成: ok={ok} err={err}")
    print(
        f"标签一致率: {stats.get('label_agreement_pct')}% "
        f"({stats.get('sample_days')} 天) | "
        f"四格一致率: {stats.get('bucket_agreement_pct')}%"
    )

    print("\n应用状态持续性确认…")
    persist = recompute_regime_persistence(
        conn, days=len(dates), min_days=args.persistence_days,
    )
    print(persist)
    conn.close()


if __name__ == "__main__":
    main()
