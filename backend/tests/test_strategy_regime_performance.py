"""L2 策略×状态矩阵单元测试。"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.regime_validation import load_jump_regime_rows
from services.strategy_regime_performance import (
    JUMP_MATRIX_SOURCE,
    _persist_cells,
    attribute_returns_by_bucket,
    compare_rule_vs_jump_matrix,
    compute_cell_metrics,
    lagged_bucket_by_date,
)


def test_lagged_bucket_by_date():
    rows = [
        {"trade_date": "2026-01-01", "bucket": "oscillation"},
        {"trade_date": "2026-01-02", "bucket": "high_vol"},
        {"trade_date": "2026-01-03", "bucket": "high_vol"},
    ]
    lagged = lagged_bucket_by_date(rows)
    assert lagged["2026-01-02"] == "oscillation"
    assert lagged["2026-01-03"] == "high_vol"


def test_attribute_and_metrics():
    lagged = {"2026-01-02": "high_vol", "2026-01-03": "high_vol", "2026-01-04": "oscillation"}
    rets = {"2026-01-02": 0.01, "2026-01-03": 0.02, "2026-01-04": -0.005}
    grouped = attribute_returns_by_bucket(rets, lagged)
    m = compute_cell_metrics(grouped["high_vol"])
    assert m["sample_days"] == 2
    assert m["win_rate_pct"] == 100.0
    assert m["sharpe"] is not None


def test_load_jump_regime_rows():
    from datetime import date, timedelta

    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    conn = sqlite3.connect(f.name)
    conn.executescript("""
        CREATE TABLE market_regime_jump_daily (
            trade_date TEXT PRIMARY KEY,
            index_code TEXT,
            jump_state INTEGER,
            regime_bucket TEXT,
            jump_penalty REAL,
            backend TEXT,
            model_version TEXT,
            updated_at TEXT
        );
    """)
    buckets = ["oscillation", "high_vol", "trend_up"]
    start = date(2026, 1, 1)
    for i in range(36):
        d = (start + timedelta(days=i)).isoformat()
        conn.execute(
            "INSERT INTO market_regime_jump_daily (trade_date, regime_bucket) VALUES (?, ?)",
            (d, buckets[i % 3]),
        )
    conn.commit()
    rows = load_jump_regime_rows(conn, days=730)
    conn.close()
    assert len(rows) == 36
    assert rows[0]["bucket"] == "oscillation"
    assert rows[-1]["trade_date"] == (start + timedelta(days=35)).isoformat()


def test_persist_cells_scoped_by_source():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    conn = sqlite3.connect(f.name)
    conn.executescript("""
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
    as_of = "2026-07-27"
    _persist_cells(
        conn,
        [{"strategy": "momentum", "bucket": "trend_up", "source": "backtest", "sample_days": 50, "sharpe": 1.2}],
        as_of=as_of,
        lookback_days=730,
        backtest_days=500,
    )
    _persist_cells(
        conn,
        [{"strategy": "turtle", "bucket": "high_vol", "source": JUMP_MATRIX_SOURCE, "sample_days": 40, "sharpe": 0.8}],
        as_of=as_of,
        lookback_days=730,
        backtest_days=500,
    )
    rule_n = conn.execute(
        "SELECT COUNT(*) FROM strategy_regime_metrics WHERE source='backtest'",
    ).fetchone()[0]
    jump_n = conn.execute(
        "SELECT COUNT(*) FROM strategy_regime_metrics WHERE source=?",
        (JUMP_MATRIX_SOURCE,),
    ).fetchone()[0]
    _persist_cells(
        conn,
        [{"strategy": "composite", "bucket": "oscillation", "source": "backtest", "sample_days": 60, "sharpe": 0.5}],
        as_of=as_of,
        lookback_days=730,
        backtest_days=500,
    )
    rule_after = conn.execute(
        "SELECT strategy_id FROM strategy_regime_metrics WHERE source='backtest' ORDER BY strategy_id",
    ).fetchall()
    conn.close()
    assert rule_n == 1
    assert jump_n == 1
    assert [r[0] for r in rule_after] == ["composite"]


def test_compare_rule_vs_jump_matrix():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    conn = sqlite3.connect(f.name)
    conn.executescript("""
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
    as_of = "2026-07-27"
    for src, strat, bucket, sharpe in [
        ("backtest", "momentum", "trend_up", 1.5),
        ("backtest", "turtle", "high_vol", 1.2),
        (JUMP_MATRIX_SOURCE, "turtle", "trend_up", 1.8),
        (JUMP_MATRIX_SOURCE, "momentum", "high_vol", 0.9),
    ]:
        conn.execute(
            """INSERT INTO strategy_regime_metrics
               (strategy_id, regime_bucket, source, portfolio_id, sample_days, sharpe,
                as_of_date, lookback_days, backtest_days, updated_at)
               VALUES (?,?,?,0,50,?,?,730,500,datetime('now'))""",
            (strat, bucket, src, sharpe, as_of),
        )
    conn.commit()
    report = compare_rule_vs_jump_matrix(conn, as_of_date=as_of)
    conn.close()
    assert report["ranking_flips"] >= 1
    assert report["as_of_date"] == as_of
