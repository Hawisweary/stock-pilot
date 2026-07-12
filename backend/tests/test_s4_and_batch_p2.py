"""S4 表达式/GP + P2 batch 补算"""
from __future__ import annotations

import sqlite3

import pytest


def test_validate_timeseries_expression():
    from services.factor_expression import validate_expression

    ok = validate_expression("Mean($adj_close, 20) / Std($adj_close, 20)")
    assert ok["valid"] is True
    assert ok["kind"] == "timeseries"
    bad = validate_expression("import os")
    assert bad["valid"] is False


def test_validate_cross_section_expression():
    from services.factor_expression import validate_expression

    ok = validate_expression("F001 * 0.5 + F002 * 0.5")
    assert ok["valid"] is True
    assert ok["kind"] == "cross_section"


def test_debate_batch_log_query(tmp_path, monkeypatch):
    db_path = tmp_path / "debate.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("services.debate_batch_log.config.DB_PATH", str(db_path))

    from services.debate_batch_log import ensure_table, log_batch_start, query_debate_history

    ensure_table()
    log_batch_start(
        job_id="j1",
        mode="tiered",
        plan={"calc_date": "2026-05-29", "today": "2026-05-29", "total": 10, "to_run": 5},
    )
    rows = query_debate_history(limit=5, job_id="j1")
    assert len(rows) == 1
    assert rows[0]["event_type"] == "start"


def test_factor_percentile_cache(tmp_path, monkeypatch):
    db_path = tmp_path / "pct.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE stocks (id INTEGER PRIMARY KEY, is_active INTEGER DEFAULT 1, industry TEXT, industry_sw TEXT);
        CREATE TABLE financial_indicators (
            stock_id INTEGER, calc_date TEXT, roe REAL, gross_margin REAL, net_margin REAL,
            pe_ttm REAL, pb REAL, dividend_yield REAL, debt_to_equity REAL, current_ratio REAL
        );
        CREATE TABLE financial_reports (stock_id INTEGER, period_end_date TEXT, revenue REAL, net_profit REAL, eps REAL, operating_cf REAL, report_type TEXT);
        CREATE TABLE valuation_snapshots (stock_id INTEGER, as_of_date TEXT, pe_ttm REAL, pb REAL, market_cap REAL, dividend_yield REAL, peg_ratio REAL, ps_ratio REAL);
        CREATE TABLE factor_weights (id INTEGER PRIMARY KEY CHECK (id=1), weight_quality REAL, weight_growth REAL, weight_value REAL, weight_momentum REAL, weight_safety REAL);
        INSERT INTO stocks VALUES (1,1,'银行','银行'),(2,1,'银行','银行'),(3,1,'医药','医药');
        INSERT INTO factor_weights VALUES (1,0.3,0.25,0.2,0.1,0.15);
        INSERT INTO financial_indicators
            (stock_id, calc_date, roe, gross_margin, net_margin, pe_ttm, pb, dividend_yield, debt_to_equity, current_ratio)
        VALUES
            (1,'2026-05-29',15,30,10,12,1.2,2,0.4,1.5),
            (2,'2026-05-29',12,28,8,15,1.5,1.5,0.5,1.2),
            (3,'2026-05-29',8,25,5,20,2,1,0.6,1.1);
        """
    )
    conn.commit()
    conn.close()

    from services.factor_percentile_cache import get_universe_metrics, invalidate

    invalidate()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    data = get_universe_metrics("2026-05-29", conn)
    conn.close()
    assert len(data["universe_ids"]) == 3


def test_execute_prefetch_skips_empty():
    from services.score_gap_prefetch import execute_prefetch

    r = execute_prefetch("policy_score", [])
    assert r["attempted"] == 0
