#!/usr/bin/env python3
"""查看 score_gap_log 审计记录"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import config

config.DB_PATH = os.environ.get("AFR_DB_PATH", os.path.join(ROOT, "data", "afr.db"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Query score_gap_log")
    parser.add_argument("--last", type=int, default=20)
    parser.add_argument("--target-date", dest="target_date", default=None)
    parser.add_argument("--trend", action="store_true", help="7 天 sync_rate 趋势")
    parser.add_argument("--cleanup", action="store_true", help="清理过期日志")
    args = parser.parse_args()

    from services.score_gap_log import cleanup_old_logs, query_gap_history, sync_rate_trend

    if args.cleanup:
        print(json.dumps(cleanup_old_logs(), ensure_ascii=False, indent=2))
        return 0
    if args.trend:
        print(json.dumps(sync_rate_trend(days=7), ensure_ascii=False, indent=2))
    else:
        rows = query_gap_history(limit=args.last, target_date=args.target_date)
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
