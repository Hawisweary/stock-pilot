#!/usr/bin/env python3
"""CLI：批量维度补算（sync_only / compute_and_sync / force_recompute）"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import config

config.DB_PATH = os.environ.get("AFR_DB_PATH", os.path.join(ROOT, "data", "afr.db"))

import database as db

db.init()


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch fill comprehensive dimension scores")
    parser.add_argument(
        "--mode",
        default="sync_only",
        choices=["sync_only", "compute_and_sync", "force_recompute"],
    )
    parser.add_argument("--target-date", dest="target_date", default=None)
    parser.add_argument("--stock-ids", dest="stock_ids", default=None, help="逗号分隔 stock_id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--wait", action="store_true", help="入队后等待 job 完成")
    args = parser.parse_args()

    stock_ids = None
    if args.stock_ids:
        stock_ids = [int(x.strip()) for x in args.stock_ids.split(",") if x.strip()]

    if args.dry_run:
        from services.batch_score_orchestrator import fill_gaps

        result = fill_gaps(
            mode=args.mode,
            stock_ids=stock_ids,
            target_date=args.target_date,
            dry_run=True,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    from services.job_queue import enqueue_batch_fill, get_job

    job = enqueue_batch_fill(
        {
            "mode": args.mode,
            "stock_ids": stock_ids,
            "target_date": args.target_date,
            "triggered_by": "cli",
        }
    )
    print(json.dumps({"job_id": job.id, "status": "queued"}, ensure_ascii=False, indent=2))

    if not args.wait:
        return 0

    while True:
        j = get_job(job.id)
        if not j or j.status.value in ("done", "failed", "cancelled"):
            break
        time.sleep(2)

    j = get_job(job.id)
    print(json.dumps(
        {
            "job_id": job.id,
            "status": j.status.value if j else "unknown",
            "result": j.result if j else None,
            "error": j.error if j else None,
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0 if j and j.status.value == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
