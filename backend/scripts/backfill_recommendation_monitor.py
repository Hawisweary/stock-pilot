#!/usr/bin/env python3
"""P1：回溯 L3 推荐历史 + 填充命中率 outcomes。"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from migrations import run_migrations
from services.strategy_recommendation_monitor import (
    backfill_recommendation_history,
    get_monitoring_dashboard,
    update_recommendation_outcomes,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="L3 推荐监控 bootstrap")
    parser.add_argument("--days", type=int, default=730)
    parser.add_argument("--clear", action="store_true", help="清空已有推荐/切换/outcome 后重填")
    parser.add_argument("--skip-backfill", action="store_true")
    parser.add_argument("--skip-outcomes", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(config.DB_PATH, timeout=120)
    conn.execute("PRAGMA busy_timeout=60000")
    run_migrations(conn)

    result: dict = {}
    if not args.skip_backfill:
        print(f"[backfill] {args.days} days…")
        result["backfill"] = backfill_recommendation_history(
            conn, days=args.days, clear_existing=args.clear,
        )
        print(json.dumps(result["backfill"], ensure_ascii=False, indent=2))

    if not args.skip_outcomes:
        print("[outcomes] updating forward returns…")
        result["outcomes"] = update_recommendation_outcomes(conn, max_rows=2000)
        print(json.dumps(result["outcomes"], ensure_ascii=False, indent=2))

    result["dashboard"] = get_monitoring_dashboard(conn, days=min(args.days, 365))
    print("\n[monitoring dashboard]")
    print(json.dumps(result["dashboard"], ensure_ascii=False, indent=2))
    conn.close()


if __name__ == "__main__":
    main()
