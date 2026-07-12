#!/usr/bin/env python3
"""查看 debate_batch_log 审计记录"""
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
    parser = argparse.ArgumentParser(description="Query debate_batch_log")
    parser.add_argument("--last", type=int, default=20)
    parser.add_argument("--target-date", dest="target_date", default=None)
    parser.add_argument("--job-id", dest="job_id", default=None)
    parser.add_argument("--event-type", dest="event_type", default=None)
    args = parser.parse_args()

    from services.debate_batch_log import query_debate_history

    rows = query_debate_history(
        limit=args.last,
        target_date=args.target_date,
        job_id=args.job_id,
        event_type=args.event_type,
    )
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
