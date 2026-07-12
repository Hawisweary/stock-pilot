"""Phase 2 batch-fill 单元测试"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_dry_run_returns_plan_without_db_write(gap_db):
    from services.batch_score_orchestrator import fill_gaps

    plan = fill_gaps(mode="sync_only", target_date="2026-05-31", dry_run=True)
    assert plan["dry_run"] is True
    assert "planned_actions" in plan
    assert plan["target_date"] == "2026-05-31"


def test_estimate_action_ms_technical_range():
    from services.batch_score_plan import estimate_action_ms

    est = estimate_action_ms("technical_score", 10, active_stocks=54, would_fetch_count=3)
    assert est["estimated_ms_range"][1] > est["estimated_ms_range"][0]


def test_job_queue_cancel():
    from services.job_queue import Job, JobStatus, cancel_job, get_job

    job = Job(id="test-cancel-1", job_type="test", payload={}, status=JobStatus.PENDING)
    from services import job_queue

    job_queue._jobs[job.id] = job
    assert cancel_job(job.id) is True
    assert get_job(job.id).status == JobStatus.CANCELLED


def test_score_gap_log_roundtrip(gap_db):
    from services.score_gap_log import log_scan, query_gap_history
    from services.score_gap_scanner import scan_gaps

    report = scan_gaps(target_date="2026-05-31")
    log_id = log_scan(report, triggered_by="test")
    assert log_id > 0
    rows = query_gap_history(limit=5, target_date="2026-05-31")
    assert any(r["event_type"] == "scan" for r in rows)


def test_can_enqueue_batch_fill_when_idle():
    from services.job_queue import can_enqueue_batch_fill, find_active_batch_fill

    active = find_active_batch_fill()
    if active is None:
        ok, _, _ = can_enqueue_batch_fill()
        assert ok is True


def test_dry_run_does_not_mutate_comprehensive(gap_db):
    import sqlite3

    from services.batch_score_orchestrator import fill_gaps

    conn = sqlite3.connect(gap_db)
    before = conn.execute(
        "SELECT stock_id, capital_score, policy_score FROM comprehensive_scores ORDER BY stock_id"
    ).fetchall()
    conn.close()

    fill_gaps(mode="compute_and_sync", target_date="2026-05-31", dry_run=True)

    conn = sqlite3.connect(gap_db)
    after = conn.execute(
        "SELECT stock_id, capital_score, policy_score FROM comprehensive_scores ORDER BY stock_id"
    ).fetchall()
    conn.close()
    assert before == after


def test_sync_only_restores_missing_capital(gap_db):
    import sqlite3

    from services.batch_score_orchestrator import fill_gaps

    conn = sqlite3.connect(gap_db)
    conn.execute(
        "UPDATE comprehensive_scores SET capital_score=NULL WHERE stock_id=1 AND calc_date='2026-05-31'"
    )
    conn.commit()
    conn.close()

    result = fill_gaps(mode="sync_only", target_date="2026-05-31")
    assert result["after"]["sync_rate_required"] == 1.0

    conn = sqlite3.connect(gap_db)
    row = conn.execute(
        "SELECT capital_score FROM comprehensive_scores WHERE stock_id=1 AND calc_date='2026-05-31'"
    ).fetchone()
    conn.close()
    assert row[0] == 75.0


def test_score_sync_health_payload(gap_db):
    from services.score_sync_health import get_score_sync_health

    health = get_score_sync_health(target_date="2026-05-31")
    assert health["target_date"] == "2026-05-31"
    assert "sync_rate_required" in health
    assert "gaps_by_dimension" in health
    assert "trend_7d" in health
    assert health["alert"]["active"] is False or isinstance(health["alert"]["active"], bool)


def test_sync_rate_trend_daily(gap_db):
    from services.score_gap_log import log_scan, sync_rate_trend_daily
    from services.score_gap_scanner import scan_gaps

    report = scan_gaps(target_date="2026-05-31")
    log_scan(report, triggered_by="test")
    trend = sync_rate_trend_daily(days=7)
    assert isinstance(trend, list)
    if trend:
        assert "date" in trend[0]
        assert "sync_rate_required" in trend[0]

