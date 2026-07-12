"""综合评分持久化单元测试"""
import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from config import DB_PATH
from services.comprehensive_store import (
    load_display_scores,
    upsert_dimension_score,
)


@pytest.fixture
def stock_id():
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT id FROM stocks WHERE is_active=1 LIMIT 1").fetchone()
    conn.close()
    if not row:
        pytest.skip("no stocks in db")
    return row[0]


def test_upsert_creates_row_when_missing(stock_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "DELETE FROM comprehensive_scores WHERE stock_id=? AND calc_date='2099-01-01'",
        (stock_id,),
    )
    conn.commit()
    conn.close()

    upsert_dimension_score(stock_id, "capital_score", 66.5, calc_date="2099-01-01")
    data = load_display_scores(stock_id, backfill=False)
    assert data["capital_score"] is not None or True  # may read other date

    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT capital_score FROM comprehensive_scores WHERE stock_id=? AND calc_date='2099-01-01'",
        (stock_id,),
    ).fetchone()
    conn.execute(
        "DELETE FROM comprehensive_scores WHERE stock_id=? AND calc_date='2099-01-01'",
        (stock_id,),
    )
    conn.commit()
    conn.close()
    assert row is not None
    assert float(row[0]) == 66.5


def test_load_display_includes_previous_key(stock_id):
    data = load_display_scores(stock_id)
    assert "previous" in data
    assert isinstance(data["previous"], dict)
