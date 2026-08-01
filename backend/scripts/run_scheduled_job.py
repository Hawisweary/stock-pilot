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


def _run_script_subprocess(script_name: str, args: list[str], timeout_sec: int = 1800) -> str:
    """运行 backend/scripts 目录下的脚本，返回最后一行输出。"""
    import os
    import subprocess
    import sys

    script = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "scripts",
        script_name,
    )
    r = subprocess.run(
        [sys.executable, script] + args,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
    )
    lines = [ln for ln in (r.stdout or "").strip().split("\n") if ln.strip()]
    last = lines[-1] if lines else ""
    if r.returncode != 0:
        raise RuntimeError(f"exit={r.returncode} stderr={(r.stderr or '')[-300:]}")
    return last


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("job", choices=["nightly", "weekly", "daily", "morning", "technical_retry", "warm_cache", "warm_decay", "regime_l2_l3"])
    parser.add_argument("--factor", default=None, help="warm_decay: 因子ID")
    parser.add_argument("--fd", type=int, default=20, help="warm_decay: 未来收益天数")
    args = parser.parse_args()

    # 跨进程互斥：同类作业全系统只允许一个实例（防调度误触发/保活风暴导致重复发射）
    import fcntl

    lock_path = f"/tmp/afr_job_{args.job}.lock"
    lock_file = open(lock_path, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(json.dumps({"job": args.job, "ok": False, "summary": "已有同类作业在运行,本次跳过"}, ensure_ascii=False))
        return 0

    if args.job == "nightly":
        from services.v5_data_sync import sync_v5_nightly_fetch

        r = sync_v5_nightly_fetch()
        summary = f"ok={r.get('ok')} coverage={r.get('news_event_coverage')}"

        # 日频 Tushare 数据：L2 资金流明细 + 沪深股通十大成交
        for script_name, label, extra_args in [
            ("tushare_sync_moneyflow_detail.py", "moneyflow", ["--days", "10"]),
            ("tushare_sync_hsgt_top10.py", "hsgt_top10", ["--days", "10"]),
        ]:
            try:
                last = _run_script_subprocess(script_name, extra_args, timeout_sec=1800)
                summary += f" {label}={last[:60]}"
            except Exception as e:
                summary += f" {label}_err:{str(e)[:40]}"
    elif args.job == "weekly":
        from services.v5_data_sync import sync_v5_weekly

        r = sync_v5_weekly()
        steps = r.get("steps") or {}
        errs = {k: str(v.get("error"))[:80] for k, v in steps.items() if isinstance(v, dict) and v.get("error")}
        summary = f"ok={r.get('ok')} steps={list(steps.keys())} errors={errs or '无'}"

        # 周频 Tushare 数据：业绩预告/快报 + 财报披露计划
        for script_name, label, extra_args in [
            ("tushare_sync_earnings_alerts.py", "earnings_alerts", ["--years", "1"]),
            ("tushare_sync_disclosure_dates.py", "disclosure_dates", []),
        ]:
            try:
                last = _run_script_subprocess(script_name, extra_args, timeout_sec=1800)
                summary += f" {label}={last[:60]}"
            except Exception as e:
                summary += f" {label}_err:{str(e)[:40]}"
    elif args.job == "morning":
        from services.scheduler import run_morning_tasks

        summary = run_morning_tasks()
    elif args.job == "technical_retry":
        from services.batch_score_maintenance import retry_technical_no_source

        r = retry_technical_no_source()
        summary = str(r)[:300]
    elif args.job == "warm_cache":
        # 预热 beta-health + IC tab 缓存(CPU密集,独立进程跑不阻塞 API)
        from services.beta_health import get_beta_health
        from services.factor_analysis_cache import warm_ic_tabs

        get_beta_health(force=True)
        warm_ic_tabs()
        summary = "beta-health + IC tabs 已预热"
    elif args.job == "warm_decay":
        # 针对单因子的 IC 衰减(18s CPU密集),按需在子进程计算并落缓存
        from services.factor_analysis_cache import cached_by_date, latest_any_factor_date, store
        from services.ic_engine import analyze_factor_decay

        key = f"decay:{args.factor}:{args.fd}"
        res = analyze_factor_decay(args.factor, forward_days=args.fd)
        if isinstance(res, dict) and not res.get("error"):
            store(key, 0, latest_any_factor_date(), res)
        summary = f"decay {args.factor}/{args.fd} 已缓存"
    elif args.job == "regime_l2_l3":
        import sqlite3

        import config
        from migrations import run_migrations
        from services.regime_pipeline import run_regime_l2_l3_pipeline

        conn = sqlite3.connect(config.DB_PATH, timeout=300)
        run_migrations(conn)
        r = run_regime_l2_l3_pipeline(conn, refresh_matrix=True)
        conn.close()
        summary = (
            f"ok={r.get('ok')} bucket={r.get('regime_bucket')} "
            f"primary={r.get('primary_strategy')} matrix={r.get('matrix_refreshed')}"
        )
    else:  # daily
        from services.scheduler import run_daily_tasks

        summary = str(run_daily_tasks())[:600]

    print(json.dumps({"job": args.job, "ok": True, "summary": summary}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
