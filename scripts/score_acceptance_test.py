#!/usr/bin/env python3
"""BATCH_DIMENSION_SCORE 验收脚本 — Phase 1～3 关键用例"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import config

config.DB_PATH = os.environ.get("AFR_DB_PATH", os.path.join(ROOT, "data", "afr.db"))

import database as db

db.init()


def _ok(name: str, passed: bool, detail: str = "") -> dict:
    return {"name": name, "passed": passed, "detail": detail}


def main() -> int:
    results: list[dict] = []
    calc_date = config.latest_trading_date()

    from services.score_gap_scanner import scan_gaps

    scan = scan_gaps(target_date=calc_date)
    results.append(
        _ok(
            "scan_gaps_returns_rates",
            "sync_rate_required" in scan and "stale_total" in scan,
            f"required={scan.get('sync_rate_required')} all={scan.get('sync_rate_all')}",
        )
    )

    # dry-run 不写 DB
    conn = sqlite3.connect(config.DB_PATH)
    before_cnt = conn.execute("SELECT COUNT(*) FROM comprehensive_scores").fetchone()[0]
    conn.close()
    from services.batch_score_orchestrator import fill_gaps

    fill_gaps(mode="compute_and_sync", target_date=calc_date, dry_run=True)
    conn = sqlite3.connect(config.DB_PATH)
    after_cnt = conn.execute("SELECT COUNT(*) FROM comprehensive_scores").fetchone()[0]
    conn.close()
    results.append(_ok("dry_run_no_db_write", before_cnt == after_cnt))

    # sync_only 性能
    t0 = time.perf_counter()
    sync_result = fill_gaps(mode="sync_only", target_date=calc_date)
    sync_ms = int((time.perf_counter() - t0) * 1000)
    results.append(
        _ok(
            "sync_only_under_2s",
            sync_ms < 2000,
            f"{sync_ms}ms status={sync_result.get('status')}",
        )
    )

    # overwrite=False 不覆盖 technical
    conn = sqlite3.connect(config.DB_PATH)
    row = conn.execute(
        """
        SELECT stock_id, technical_score FROM comprehensive_scores
        WHERE calc_date=? AND technical_score IS NOT NULL LIMIT 1
        """,
        (calc_date,),
    ).fetchone()
    if row:
        sid, orig = int(row[0]), float(row[1])
        sentinel = orig + 0.1 if orig < 99 else orig - 0.1
        conn.execute(
            "UPDATE comprehensive_scores SET technical_score=? WHERE stock_id=? AND calc_date=?",
            (sentinel, sid, calc_date),
        )
        conn.commit()
        conn.close()
        from services.comprehensive import sync_all_dimensions

        sync_all_dimensions(stock_ids=[sid], calc_date=calc_date, overwrite=False)
        conn = sqlite3.connect(config.DB_PATH)
        now = conn.execute(
            "SELECT technical_score FROM comprehensive_scores WHERE stock_id=? AND calc_date=?",
            (sid, calc_date),
        ).fetchone()[0]
        conn.close()
        results.append(
            _ok(
                "sync_only_preserves_technical",
                float(now) == sentinel,
                f"stock={sid} kept={sentinel}",
            )
        )
    else:
        results.append(_ok("sync_only_preserves_technical", True, "skip: no technical row"))

    # 409 限流
    from services.job_queue import Job, JobStatus, enqueue_batch_fill, find_active_batch_fill, cancel_job

    active = find_active_batch_fill()
    if active and active.status.value in ("running", "pending", "queued"):
        results.append(_ok("batch_fill_409", True, f"skip: job {active.id} already running"))
    else:
        j1 = enqueue_batch_fill({"mode": "sync_only", "target_date": calc_date, "triggered_by": "acceptance"})
        try:
            j2 = enqueue_batch_fill({"mode": "sync_only", "target_date": calc_date, "triggered_by": "acceptance"})
            results.append(_ok("batch_fill_409", False, "second enqueue should fail"))
        except RuntimeError as e:
            results.append(_ok("batch_fill_409", "已有" in str(e) or "补算" in str(e), str(e)))
        finally:
            cancel_job(j1.id)

    # prefetch
    from services.score_gap_prefetch import prefetch_if_needed

    pf = prefetch_if_needed("technical_score", [1], dry_run=True)
    results.append(
        _ok(
            "prefetch_technical_shape",
            "would_fetch" in pf and "details" in pf,
            f"would_fetch={pf.get('would_fetch')}",
        )
    )

    # dashboard health
    from services.score_sync_health import get_score_sync_health

    health = get_score_sync_health(target_date=calc_date)
    results.append(
        _ok(
            "score_sync_health",
            "gaps_by_dimension" in health and "trend_7d" in health,
            f"stale_total={health.get('stale_total')}",
        )
    )

    passed = sum(1 for r in results if r["passed"])
    report = {
        "target_date": calc_date,
        "passed": passed,
        "total": len(results),
        "all_passed": passed == len(results),
        "results": results,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
