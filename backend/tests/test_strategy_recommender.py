"""L3 策略推荐引擎测试。"""
import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.strategy_regime_performance import JUMP_MATRIX_SOURCE
from services.strategy_recommender import (
    _build_jump_opinion,
    _confidence_score,
    _persist,
    _rationale,
    get_stored_recommendation,
)


def test_confidence_score():
    primary = {"sample_days": 129, "sharpe": 2.49, "strategy": "turtle"}
    c = _confidence_score(primary, hard_rule_match=True, bucket_agreement_pct=30)
    assert 0.7 <= c <= 0.98


def test_persist_and_load():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    conn = sqlite3.connect(f.name)
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
    payload = {
        "trade_date": "2026-07-27",
        "market": {"regime_bucket": "high_vol"},
        "recommendation": {"confidence": 0.85, "primary": {"strategy": "turtle"}},
    }
    _persist(conn, payload)
    loaded = get_stored_recommendation(conn, "2026-07-27")
    assert loaded["recommendation"]["primary"]["strategy"] == "turtle"
    conn.close()


def test_rationale():
    text = _rationale("高波动", {"label": "海龟", "sharpe": 2.5, "sample_days": 100, "strategy": "turtle"}, "turtle", True)
    assert "高波动" in text
    assert "海龟" in text


def test_build_jump_opinion_diverged():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    conn = sqlite3.connect(f.name)
    conn.executescript("""
        CREATE TABLE market_regime_jump_daily (
            trade_date TEXT PRIMARY KEY,
            regime_bucket TEXT,
            jump_penalty REAL,
            model_version TEXT,
            backend TEXT
        );
        CREATE TABLE strategy_regime_metrics (
            strategy_id TEXT,
            regime_bucket TEXT,
            source TEXT,
            portfolio_id INTEGER,
            sample_days INTEGER,
            total_return_pct REAL,
            ann_return_pct REAL,
            ann_vol_pct REAL,
            sharpe REAL,
            max_drawdown_pct REAL,
            win_rate_pct REAL,
            is_recommended INTEGER,
            as_of_date TEXT,
            lookback_days INTEGER,
            backtest_days INTEGER,
            updated_at TEXT,
            PRIMARY KEY (strategy_id, regime_bucket, source, portfolio_id, as_of_date)
        );
    """)
    conn.execute(
        "INSERT INTO market_regime_jump_daily VALUES ('2026-07-27', 'oscillation', 25.0, 'jump_dynamic_wf_v1', 'simple')",
    )
    conn.execute(
        """INSERT INTO strategy_regime_metrics
           (strategy_id, regime_bucket, source, portfolio_id, sample_days, sharpe,
            as_of_date, lookback_days, backtest_days, updated_at)
           VALUES ('index_enhance', 'oscillation', ?, 0, 101, 4.01, '2026-07-27', 730, 500, datetime('now'))""",
        (JUMP_MATRIX_SOURCE,),
    )
    conn.commit()

    regime = {"trade_date": "2026-07-27", "regime_csi800_label": "高波动"}
    opinion = _build_jump_opinion(
        conn,
        regime,
        rule_bucket="high_vol",
        rule_primary={"strategy": "sector_rotation", "sharpe": 2.98},
    )
    conn.close()

    assert opinion is not None
    assert opinion["aligned"] is False
    assert opinion["bucket_diverged"] is True
    assert opinion["jump_bucket"] == "oscillation"
    assert opinion["primary"]["strategy"] == "index_enhance"
    assert "第二意见" in opinion["rationale"] or "规则" in opinion["rationale"]
