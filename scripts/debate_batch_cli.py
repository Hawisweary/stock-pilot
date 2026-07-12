#!/usr/bin/env python3
"""辩论批量 CLI — dry-run / 入队 / 等待完成"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def _load_env() -> None:
    """与 app.py 一致：加载 backend/.env 或项目根 .env"""
    import os

    root = Path(__file__).resolve().parents[1]
    for env_path in (root / "backend" / ".env", root / ".env"):
        if not env_path.exists():
            continue
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


def main() -> int:
    _load_env()
    parser = argparse.ArgumentParser(description="AFR 辩论批量")
    parser.add_argument("--dry-run", action="store_true", help="仅输出计划")
    parser.add_argument("--mode", default="tiered", choices=["full", "tiered", "changed_only", "force", "retry_failed"])
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--skip-unchanged", action="store_true", default=None)
    parser.add_argument("--no-skip", action="store_true", help="强制全量 LLM")
    parser.add_argument("--priority-top", type=int, default=None)
    parser.add_argument("--priority-bottom", type=int, default=None)
    parser.add_argument("--retry-failed", action="store_true", help="等同 --mode retry_failed")
    parser.add_argument("--retry-job-id", type=str, default=None, help="从指定 job 补跑失败项")
    parser.add_argument("--stock-ids", type=str, default=None, help="逗号分隔 id")
    parser.add_argument("--wait", action="store_true", help="入队后等待 job 完成")
    args = parser.parse_args()

    if args.retry_failed:
        args.mode = "retry_failed"

    import database as db

    if not db.is_initialized():
        db.init()

    stock_ids = None
    if args.stock_ids:
        stock_ids = [int(x.strip()) for x in args.stock_ids.split(",") if x.strip()]

    skip_unchanged = None
    if args.no_skip:
        skip_unchanged = False
    elif args.skip_unchanged:
        skip_unchanged = True

    from services.debate_orchestrator import run_debate_batch

    if args.dry_run:
        plan = run_debate_batch(
            mode=args.mode,
            stock_ids=stock_ids,
            skip_unchanged=skip_unchanged,
            priority_top_n=args.priority_top,
            priority_bottom_n=args.priority_bottom,
            retry_job_id=args.retry_job_id,
            dry_run=True,
        )
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    from services.job_queue import can_enqueue_debate_batch, enqueue_debate_batch, get_job

    ok, reason, running_id = can_enqueue_debate_batch()
    if not ok:
        print(json.dumps({"error": reason, "running_job_id": running_id}, ensure_ascii=False))
        return 1

    plan = run_debate_batch(
        mode=args.mode,
        stock_ids=stock_ids,
        skip_unchanged=skip_unchanged,
        dry_run=True,
        retry_job_id=args.retry_job_id,
    )
    payload = {
        "mode": args.mode,
        "concurrency": args.concurrency,
        "skip_unchanged": skip_unchanged,
        "stock_ids": stock_ids,
        "priority_top_n": args.priority_top,
        "priority_bottom_n": args.priority_bottom,
        "retry_job_id": args.retry_job_id,
        "triggered_by": "cli",
    }
    job = enqueue_debate_batch(payload)
    print(
        json.dumps(
            {"job_id": job.id, "plan": plan, "poll_url": f"/api/system/jobs/{job.id}"},
            ensure_ascii=False,
            indent=2,
        )
    )

    if not args.wait:
        return 0

    while True:
        j = get_job(job.id)
        if not j:
            print("job 丢失")
            return 1
        if j.status.value == "done":
            print(json.dumps(j.result, ensure_ascii=False, indent=2))
            return 0
        if j.status.value in ("failed", "cancelled"):
            print(json.dumps({"error": j.error, "result": j.result}, ensure_ascii=False))
            return 1
        prog = (j.result or {}).get("progress", {})
        if prog:
            print(f"进度 {prog.get('completed', '?')}/{prog.get('total', '?')}", flush=True)
        time.sleep(2)


if __name__ == "__main__":
    raise SystemExit(main())
