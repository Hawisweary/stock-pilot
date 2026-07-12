"""调度作业子进程入口 — 重任务在独立进程运行，不占用 API 进程线程池。

用法: python scripts/run_scheduled_job.py {nightly|weekly|daily|technical_retry}
最后一行输出 JSON 结果，供调度器捕获记录。
"""
import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("job", choices=["nightly", "weekly", "daily", "technical_retry"])
    args = parser.parse_args()

    if args.job == "nightly":
        from services.v5_data_sync import sync_v5_nightly_fetch

        r = sync_v5_nightly_fetch()
        summary = f"ok={r.get('ok')} coverage={r.get('news_event_coverage')}"
    elif args.job == "weekly":
        from services.v5_data_sync import sync_v5_weekly

        r = sync_v5_weekly()
        steps = r.get("steps") or {}
        errs = {k: str(v.get("error"))[:80] for k, v in steps.items() if isinstance(v, dict) and v.get("error")}
        summary = f"ok={r.get('ok')} steps={list(steps.keys())} errors={errs or '无'}"
    elif args.job == "technical_retry":
        from services.batch_score_maintenance import retry_technical_no_source

        r = retry_technical_no_source()
        summary = str(r)[:300]
    else:  # daily
        from services.scheduler import run_daily_tasks

        summary = str(run_daily_tasks())[:600]

    print(json.dumps({"job": args.job, "ok": True, "summary": summary}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
