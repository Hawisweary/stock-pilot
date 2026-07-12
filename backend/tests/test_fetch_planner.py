"""FetchPlanner 单元测试"""
from datetime import datetime, timedelta

import pytest

from services.fetch_planner import build_plan, quote_bars_for_stock


@pytest.fixture
def planner_conn(tmp_path):
    import sqlite3

    db = sqlite3.connect(str(tmp_path / "planner.db"))
    db.executescript(
        """
        CREATE TABLE stocks (id INTEGER PRIMARY KEY, industry TEXT, industry_sw TEXT);
        CREATE TABLE stock_daily_quotes (
            stock_id INTEGER, trade_date TEXT, close REAL,
            PRIMARY KEY (stock_id, trade_date)
        );
        CREATE TABLE data_fetch_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER, data_type TEXT, status TEXT, fetch_time TEXT
        );
        """
    )
    db.execute(
        "INSERT INTO stocks (id, industry, industry_sw) VALUES (1, '银行', '银行')"
    )
    db.commit()
    yield db
    db.close()


def test_incremental_skips_financials_when_recent(planner_conn):
    plan = build_plan(
        1,
        "incremental",
        last_success={"financials": datetime.now() - timedelta(days=5)},
        meta={"industry": "银行", "industry_sw": "银行"},
    )
    assert plan.fetch_financials is False
    assert plan.fetch_info is False
    assert "financials" in plan.skipped_steps


def test_full_mode_pulls_everything(planner_conn):
    plan = build_plan(1, "full")
    assert plan.mode == "full"
    assert plan.fetch_financials is True
    assert plan.skip_factor is True
    assert plan.batch_commit is True


def test_quote_bars_incremental(planner_conn):
    old = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    planner_conn.execute(
        "INSERT INTO stock_daily_quotes (stock_id, trade_date, close) VALUES (1, ?, 10.0)",
        (old,),
    )
    planner_conn.commit()
    bars = quote_bars_for_stock(planner_conn, 1, max_bars=500, incremental=True)
    assert 15 <= bars < 500
