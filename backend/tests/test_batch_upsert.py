"""批量 upsert 稳定性测试"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_upsert_dimension_scores_batch_single_transaction(gap_db):
    from services.comprehensive_store import upsert_dimension_scores_batch

    updates = {
        1: {"capital_score": 77.0, "policy_score": 61.0},
        2: {"capital_score": 73.0, "mood_score": 51.0},
    }
    changed_ids = upsert_dimension_scores_batch(updates, "2026-05-31")
    # v3.0: 返回 list[int] changed_ids（供 Path B V5 重算）
    assert isinstance(changed_ids, list)
    assert set(changed_ids) == {1, 2}

    import sqlite3

    conn = sqlite3.connect(gap_db)
    conn.row_factory = sqlite3.Row
    r1 = conn.execute(
        "SELECT capital_score, policy_score, composite_score FROM comprehensive_scores WHERE stock_id=1 AND calc_date='2026-05-31'"
    ).fetchone()
    r2 = conn.execute(
        "SELECT capital_score, mood_score FROM comprehensive_scores WHERE stock_id=2 AND calc_date='2026-05-31'"
    ).fetchone()
    conn.close()

    assert r1["capital_score"] == 77.0
    assert r1["policy_score"] == 61.0
    # v3.0: composite_score 不再自动写入（已废弃）
    assert r2["capital_score"] == 73.0
    assert r2["mood_score"] == 51.0
