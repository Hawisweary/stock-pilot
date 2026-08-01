"""P1 推荐监控测试。"""
import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.strategy_recommendation_monitor import (
    compute_forward_return,
    ensure_outcome_placeholders,
    log_regime_switch,
)
from services.strategy_recommender import _persist


def _setup_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE strategy_recommendations_daily (
            trade_date TEXT PRIMARY KEY,
            regime_bucket TEXT,
            primary_strategy TEXT,
            confidence REAL,
            payload_json TEXT NOT NULL,
            updated_at TEXT
        );
        CREATE TABLE regime_switch_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            prev_bucket TEXT,
            new_bucket TEXT NOT NULL,
            prev_strategy TEXT,
            new_strategy TEXT,
            bucket_changed INTEGER NOT NULL DEFAULT 0,
            strategy_changed INTEGER NOT NULL DEFAULT 0,
            confidence REAL,
            note TEXT,
            created_at TEXT
        );
        CREATE TABLE strategy_recommendation_outcomes (
            trade_date TEXT NOT NULL,
            horizon_days INTEGER NOT NULL,
            regime_bucket TEXT,
            primary_strategy TEXT NOT NULL,
            confidence REAL,
            strategy_return_pct REAL,
            benchmark_return_pct REAL,
            excess_return_pct REAL,
            hit INTEGER,
            evaluated_at TEXT,
            matrix_as_of TEXT,
            updated_at TEXT,
            PRIMARY KEY (trade_date, horizon_days)
        );
    """)


def test_compute_forward_return():
    rets = {
        "2026-01-02": 0.01,
        "2026-01-03": 0.02,
        "2026-01-06": -0.01,
        "2026-01-07": 0.005,
    }
    out = compute_forward_return(rets, "2026-01-01", 3)
    assert out is not None
    expected = (1.01 * 1.02 * 0.99) - 1
    assert abs(out - expected) < 1e-9


def test_log_regime_switch_on_bucket_change():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    conn = sqlite3.connect(f.name)
    _setup_db(conn)
    conn.execute(
        """INSERT INTO strategy_recommendations_daily
           (trade_date, regime_bucket, primary_strategy, confidence, payload_json, updated_at)
           VALUES ('2026-07-25', 'oscillation', 'composite', 0.8, '{}', datetime('now'))""",
    )
    conn.commit()

    sw = log_regime_switch(
        conn,
        trade_date="2026-07-27",
        new_bucket="high_vol",
        new_strategy="turtle",
        confidence=0.85,
    )
    conn.commit()
    assert sw is not None
    assert sw["bucket_changed"] is True
    assert sw["strategy_changed"] is True

    row = conn.execute("SELECT COUNT(*) FROM regime_switch_log").fetchone()[0]
    assert row == 1
    conn.close()


def test_persist_creates_outcome_placeholders():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    conn = sqlite3.connect(f.name)
    _setup_db(conn)
    payload = {
        "trade_date": "2026-07-27",
        "matrix_as_of": "2026-07-27",
        "market": {"regime_bucket": "high_vol"},
        "recommendation": {"confidence": 0.85, "primary": {"strategy": "composite"}},
    }
    _persist(conn, payload)
    n = conn.execute("SELECT COUNT(*) FROM strategy_recommendation_outcomes").fetchone()[0]
    assert n >= 2
    conn.close()


def test_ensure_outcome_placeholders_idempotent():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    conn = sqlite3.connect(f.name)
    _setup_db(conn)
    ensure_outcome_placeholders(
        conn,
        trade_date="2026-07-01",
        regime_bucket="oscillation",
        primary_strategy="composite",
        confidence=0.7,
        horizons=(5, 20),
    )
    ensure_outcome_placeholders(
        conn,
        trade_date="2026-07-01",
        regime_bucket="oscillation",
        primary_strategy="composite",
        confidence=0.7,
        horizons=(5, 20),
    )
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM strategy_recommendation_outcomes").fetchone()[0]
    assert n == 2
    conn.close()
