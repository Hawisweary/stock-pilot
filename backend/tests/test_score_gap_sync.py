"""Phase 1 维度缺口扫描与 sync_only 单元测试"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_scan_gaps_detects_missing(gap_db):
    from services.score_gap_scanner import scan_gaps

    report = scan_gaps(target_date="2026-05-31")
    assert report["active_stocks_count"] == 2
    assert report["sync_rate_required"] < 1.0
    assert report["missing_total"] > 0
    assert report["summary"]["fundamental_score"]["missing"] == 2


def test_sync_only_fills_without_overwrite(gap_db):
    from services.comprehensive import sync_all_dimensions
    from services.score_gap_scanner import scan_gaps

    before = scan_gaps(target_date="2026-05-31")
    result = sync_all_dimensions(calc_date="2026-05-31", overwrite=False)
    after = scan_gaps(target_date="2026-05-31")

    assert result["unchanged"]["technical_score"] >= 1
    assert after["sync_rate_required"] == 1.0
    assert before["sync_rate_required"] < after["sync_rate_required"]

    conn = sqlite3.connect(gap_db)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT technical_score, fundamental_score FROM comprehensive_scores WHERE stock_id=1"
    ).fetchone()
    conn.close()
    assert row["technical_score"] == 65.0
    assert row["fundamental_score"] == 80.0


def test_batch_fill_guard_blocks_can_run_sync(monkeypatch):
    from services.batch_score_guard import batch_fill_session, can_run_sync

    monkeypatch.setattr("services.job_queue.find_active_batch_fill", lambda: None)
    assert can_run_sync()[0] is True
    with batch_fill_session():
        assert can_run_sync()[0] is False
    assert can_run_sync()[0] is True


def test_sentiment_aggregate_window(gap_db):
    import sqlite3

    from services.sentiment_aggregate import batch_get_sentiment_scores

    conn = sqlite3.connect(gap_db)
    scores = batch_get_sentiment_scores(conn, [1], "2026-05-31")
    conn.close()
    assert 1 in scores
    assert scores[1] == 0.8
