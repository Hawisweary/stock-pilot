"""新策略与增强：反转、红利防御、动量崩溃、V5 profile、双均线 F031。"""
from __future__ import annotations

import sqlite3
import sys
import os
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_regime_profile_weights():
    import config
    from services.v5_scorer import _regime_weights

    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE market_regime_daily (
            trade_date TEXT, regime TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO market_regime_daily VALUES (?, ?)",
        (date.today().isoformat(), "strong_trend_up"),
    )
    w = _regime_weights(conn, calc_date=date.today().isoformat())
    conn.close()
    assert w["technical"] == pytest.approx(config.V5_PROFILE_WEIGHTS["momentum"]["technical"], rel=0.01)
    assert w["quality"] == pytest.approx(config.V5_PROFILE_WEIGHTS["momentum"]["quality"], rel=0.01)


def test_momentum_crash_reason():
    from services.strategy_selector import momentum_crash_reason

    dates = [(date.today() - timedelta(days=i)).isoformat() for i in range(25, -1, -1)]
    rows = []
    price = 100.0
    for i, d in enumerate(dates):
        if i >= len(dates) - 10:
            price *= 0.95
        rows.append((d, price, 1_000_000))
    rows_desc = list(reversed(rows))
    assert momentum_crash_reason(rows_desc) == "momentum_crash"


def test_ma_crossover_filtered_ranging():
    from services.ohlcv_technical_factors import _ma_crossover_filtered

    closes = [10.0] * 30
    highs = [10.2] * 30
    lows = [9.8] * 30
    # 震荡：ADX 低
    assert _ma_crossover_filtered({"closes": closes, "highs": highs, "lows": lows}) == 0.0


def test_sector_crowding_block_signal():
    from services.sector_rotation import _sector_crowding_score

    score = _sector_crowding_score(5.0, 5.0, 2.5, 8.0)
    assert score >= 70


@pytest.fixture
def svc_env(tmp_path, monkeypatch):
    monkeypatch.setattr("services.trade_pricing._fetch_realtime", lambda code: None)
    monkeypatch.setattr("services.data_sources.tencent_quote", lambda codes: {})
    db_file = str(tmp_path / "test.db")
    import config

    monkeypatch.setattr(config, "DB_PATH", db_file)
    import importlib
    import db_util
    import services.portfolio_svc as svc

    importlib.reload(db_util)
    importlib.reload(svc)

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    svc._ensure_tables(conn)
    today = date.today().isoformat()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE, name TEXT,
            is_active INTEGER DEFAULT 1, industry_sw TEXT
        );
        CREATE TABLE IF NOT EXISTS stock_daily_quotes (
            stock_id INTEGER, trade_date TEXT, open REAL, close REAL,
            high REAL, low REAL, volume REAL, turnover REAL,
            UNIQUE(stock_id, trade_date)
        );
        CREATE TABLE IF NOT EXISTS comprehensive_scores (
            stock_id INTEGER, calc_date TEXT, composite_v5 REAL, quality_score REAL,
            val_score REAL, veto_status TEXT
        );
        CREATE TABLE IF NOT EXISTS valuation_snapshots (
            stock_id INTEGER, as_of_date TEXT, dividend_yield REAL, dividend_yield_ttm REAL
        );
        CREATE TABLE IF NOT EXISTS factor_values (
            stock_id INTEGER, factor_id TEXT, date TEXT, value REAL,
            UNIQUE(stock_id, factor_id, date)
        );
        CREATE TABLE IF NOT EXISTS trade_calendar (
            cal_date TEXT PRIMARY KEY, is_open INTEGER
        );
    """)
    conn.execute("INSERT OR REPLACE INTO trade_calendar VALUES (?,1)", (today,))
    conn.commit()
    conn.close()
    from services.trade_calendar import invalidate_cache
    invalidate_cache()
    yield svc, db_file


def test_select_reversal_and_dividend(svc_env):
    svc, db_file = svc_env
    from services.strategy_selector import select_top_n

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    today = date.today().isoformat()
    conn.execute("INSERT INTO stocks (code,name,is_active) VALUES ('000001','A',1)")
    sid = conn.execute("SELECT id FROM stocks WHERE code='000001'").fetchone()[0]
    conn.execute(
        "INSERT INTO factor_values VALUES (?,?,?,?)",
        (sid, "F020", today, 5.5),
    )
    conn.execute(
        "INSERT INTO comprehensive_scores VALUES (?,?,?,?,?,?)",
        (sid, today, 70, 65, 60, None),
    )
    conn.execute(
        "INSERT INTO valuation_snapshots VALUES (?,?,?,?)",
        (sid, today, 3.5, None),
    )
    conn.commit()
    rev, err = select_top_n(conn, strategy="reversal", top_n=5, min_score=0)
    assert not err, err
    assert len(rev) == 1

    div, err2 = select_top_n(conn, strategy="dividend_defensive", top_n=5, min_score=54)
    assert not err2, err2
    assert len(div) == 1
    conn.close()
