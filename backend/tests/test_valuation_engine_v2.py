"""估值引擎 v2 — 行业内分位 + winsorize。"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def val_db(tmp_path, monkeypatch):
    db_path = tmp_path / "val.db"
    import config

    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE stocks (
            id INTEGER PRIMARY KEY, code TEXT, is_active INTEGER DEFAULT 1,
            industry_sw TEXT, industry_sw2 TEXT
        );
        CREATE TABLE valuation_snapshots (
            stock_id INTEGER, as_of_date TEXT, pe_ttm REAL, pb REAL,
            market_cap REAL, dividend_yield REAL
        );
        INSERT INTO stocks VALUES
            (1, '601398', 1, '银行', '国有大型银行'),
            (2, '300750', 1, '电力设备', '电池'),
            (3, '601899', 1, '有色金属', '工业金属');
        INSERT INTO valuation_snapshots VALUES
            (1, '2026-06-04', 6.0, 0.6, 1e12, 0.05),
            (2, '2026-06-04', 35.0, 5.0, 1e11, 0.0),
            (3, '2026-06-04', 18.0, 2.5, 5e10, 0.02);
        """
    )
    conn.commit()
    conn.close()
    return db_path


def test_industry_percentile_scores(val_db, monkeypatch):
    import config
    from services.valuation_engine import compute_valuation_scores

    monkeypatch.setattr(config, "DB_PATH", str(val_db))
    r = compute_valuation_scores(sync_comprehensive=False, score_date="2026-06-08")
    assert r["computed"] == 3

    conn = sqlite3.connect(str(val_db))
    bank = conn.execute(
        "SELECT composite_score, breakdown_json FROM valuation_scores WHERE stock_id=1"
    ).fetchone()
    batt = conn.execute(
        "SELECT composite_score, breakdown_json FROM valuation_scores WHERE stock_id=2"
    ).fetchone()
    conn.close()

    assert bank[0] is not None
    assert batt[0] is not None
    bank_bd = json.loads(bank[1])
    assert bank_bd.get("industry") == "国有大型银行"
    assert bank_bd.get("industry_sample_n", 0) >= 1
