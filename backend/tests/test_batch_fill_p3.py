"""Phase 3 batch-fill 增强测试"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_sentiment_keyword_score():
    from services.news_fetcher import score_text_keywords

    score, label = score_text_keywords("公司盈利增长超预期，回购增持")
    assert score > 50
    score2, _ = score_text_keywords("亏损下滑立案调查")
    assert score2 < 50


def test_resolve_sentiment_fallback(gap_db):
    import sqlite3

    from services.sentiment_aggregate import resolve_sentiment_scores

    conn = sqlite3.connect(gap_db)
    conn.execute(
        "INSERT INTO stock_news (stock_id, pub_date, sentiment_score) VALUES (1, '2026-05-01', 70)"
    )
    conn.commit()
    scores = resolve_sentiment_scores(conn, [1], "2026-05-31")
    conn.close()
    assert 1 in scores
    assert scores[1] > 0

    from services.score_gap_scanner import is_source_stale

    target = "2026-05-31"
    assert is_source_stale("2026-05-30", target, stale_days=1) is False
    assert is_source_stale("2026-05-28", target, stale_days=1) is True
    assert is_source_stale(None, target) is False


def test_scan_detects_stale_dimension(gap_db, monkeypatch):
    import sqlite3

    from services.score_gap_scanner import scan_gaps

    old = (datetime.strptime("2026-05-31", "%Y-%m-%d") - timedelta(days=5)).strftime("%Y-%m-%d")
    conn = sqlite3.connect(gap_db)
    conn.execute(
        "UPDATE comprehensive_scores SET fundamental_score=80.0 WHERE stock_id=1 AND calc_date='2026-05-31'"
    )
    conn.execute(
        "UPDATE factor_scores SET calc_date=? WHERE stock_id=1",
        (old,),
    )
    conn.commit()
    conn.close()

    report = scan_gaps(target_date="2026-05-31")
    stale_gaps = [
        g for g in report["gaps"] if g["stock_id"] == 1 and g["dimension"] == "fundamental_score"
    ]
    assert any(g["status"] == "stale" for g in stale_gaps)
    assert report["stale_total"] >= 1


def test_sync_refresh_stale(gap_db, monkeypatch):
    import sqlite3

    from services.comprehensive import sync_all_dimensions
    from services.score_gap_scanner import scan_gaps

    today = "2026-05-31"
    old = "2026-05-20"
    conn = sqlite3.connect(gap_db)
    conn.execute("UPDATE factor_scores SET calc_date=?, composite_score=88.0 WHERE stock_id=1", (old,))
    conn.execute(
        "UPDATE comprehensive_scores SET fundamental_score=80.0 WHERE stock_id=1 AND calc_date=?",
        (today,),
    )
    conn.commit()
    conn.close()

    before = scan_gaps(target_date=today)
    assert before["summary"]["fundamental_score"]["stale"] >= 1

    sync_all_dimensions(calc_date=today, refresh_stale=True)
    conn = sqlite3.connect(gap_db)
    score = conn.execute(
        "SELECT fundamental_score FROM comprehensive_scores WHERE stock_id=1 AND calc_date=?",
        (today,),
    ).fetchone()[0]
    conn.close()
    assert score == 88.0


def test_cleanup_old_logs(gap_db):
    import sqlite3

    from services.score_gap_log import cleanup_old_logs, log_scan, query_gap_history
    from services.score_gap_scanner import scan_gaps

    report = scan_gaps(target_date="2026-05-31")
    log_scan(report, triggered_by="test")
    conn = sqlite3.connect(gap_db)
    conn.execute(
        "UPDATE score_gap_log SET created_at=datetime('now', '-100 days') WHERE event_type='scan'"
    )
    conn.commit()
    conn.close()

    result = cleanup_old_logs(retention_days=90)
    assert result["deleted_general"] >= 1
    rows = query_gap_history(limit=10)
    assert not any(r["event_type"] == "scan" for r in rows)


def test_prefetch_if_needed_shape(gap_db):
    from services.score_gap_prefetch import prefetch_if_needed

    r = prefetch_if_needed("policy_score", [1, 2], dry_run=True)
    assert r["would_fetch"] == 0
    assert r["attempted"] == 2

    r2 = prefetch_if_needed("technical_score", [1], dry_run=True)
    assert "details" in r2


def test_build_fill_plan_includes_prefetch(gap_db):
    from services.batch_score_plan import build_fill_plan

    plan = build_fill_plan(mode="compute_and_sync", target_date="2026-05-31")
    assert "prefetch_by_dimension" in plan
    assert isinstance(plan["prefetch_by_dimension"], dict)


def test_sync_only_preserves_technical(gap_db):
    import sqlite3

    from services.comprehensive import sync_all_dimensions

    conn = sqlite3.connect(gap_db)
    conn.execute(
        "UPDATE comprehensive_scores SET technical_score=88.8 WHERE stock_id=1 AND calc_date='2026-05-31'"
    )
    conn.commit()
    conn.close()

    sync_all_dimensions(stock_ids=[1], calc_date="2026-05-31", overwrite=False)
    conn = sqlite3.connect(gap_db)
    score = conn.execute(
        "SELECT technical_score FROM comprehensive_scores WHERE stock_id=1 AND calc_date='2026-05-31'"
    ).fetchone()[0]
    conn.close()
    assert score == 88.8


def test_run_p2_phases_parallel_smoke(gap_db):
    from services.batch_score_maintenance import run_p2_phases_parallel

    results = run_p2_phases_parallel([1, 2], "2026-05-31", ("capital_score",))
    assert "capital_score" in results
    assert "error" not in results["capital_score"]
    assert results["capital_score"].get("dimension") == "capital_score"
