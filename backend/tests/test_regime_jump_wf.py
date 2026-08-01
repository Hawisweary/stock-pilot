"""Jump Model Walk-Forward λ 选参测试。"""
import os
import sqlite3
import sys
from datetime import date, timedelta

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.regime_jump import (
    _pick_best_lambda,
    _score_lambda_window,
    fit_jump,
    walkforward_tune_lambda,
    get_jump_penalty_for_date,
    persist_lambda_timeline,
)

def _seed_regime_db(path: str, n: int = 620) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE market_regime_daily (
            trade_date TEXT PRIMARY KEY,
            regime TEXT, regime_label TEXT,
            return_20d REAL, volatility_20 REAL, adx REAL, ma20_slope REAL,
            price_vs_ma60 REAL,
            return_20d_csi800 REAL, volatility_20_csi800 REAL, adx_csi800 REAL,
            ma20_slope_csi800 REAL, price_vs_ma60_csi800 REAL,
            regime_csi800 TEXT, regime_csi800_label TEXT,
            regime_bucket_csi800 TEXT,
            regime_label_agreement INTEGER
        )"""
    )
    rng = np.random.default_rng(0)
    base = date(2023, 1, 1)
    for i in range(n):
        td = (base + timedelta(days=i)).isoformat()
        ret = float(rng.normal(0.02, 0.05))
        vol = float(abs(rng.normal(0.15, 0.03)))
        bucket = "oscillation"
        if ret > 0.06:
            bucket = "trend_up"
        elif ret < -0.04:
            bucket = "trend_down"
        elif vol > 0.22:
            bucket = "high_vol"
        conn.execute(
            """INSERT INTO market_regime_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                td, bucket, bucket, ret, vol, 30.0, 0.001, 0.01,
                ret, vol, 30.0, 0.001, 0.01, bucket, bucket, bucket, 1,
            ),
        )
    conn.commit()
    conn.close()


def test_score_lambda_window():
    rng = np.random.default_rng(1)
    X_train = rng.normal(size=(100, 5))
    X_val = rng.normal(size=(20, 5))
    val_dates = [(date(2024, 1, 1) + timedelta(days=i)).isoformat() for i in range(20)]
    rule = {d: "oscillation" for d in val_dates}
    fit = fit_jump(X_train, jump_penalty=15.0, backend="simple")
    s = _score_lambda_window(fit, val_dates, X_val, rule)
    assert 0 <= s["score"] <= 1
    assert s["oos_samples"] == 20


def test_pick_best_lambda():
    rng = np.random.default_rng(2)
    X_train = rng.normal(size=(80, 5))
    X_val = rng.normal(size=(15, 5))
    val_dates = [(date(2024, 2, 1) + timedelta(days=i)).isoformat() for i in range(15)]
    rule = {d: "oscillation" for d in val_dates}
    lam, detail, trials = _pick_best_lambda(
        X_train, val_dates, X_val, rule,
        candidates=(5.0, 10.0, 20.0),
        backend="simple",
    )
    assert lam in (5.0, 10.0, 20.0)
    assert len(trials) == 3
    assert detail["score"] >= 0


def test_walkforward_tune_lambda(tmp_path, monkeypatch):
    db = str(tmp_path / "wf.db")
    _seed_regime_db(db, n=620)
    monkeypatch.setattr("config.DB_PATH", db)

    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS jump_lambda_walkforward (
            trade_date TEXT PRIMARY KEY, jump_penalty REAL NOT NULL,
            train_start TEXT, train_end TEXT, val_start TEXT, val_end TEXT,
            window_score REAL, consistency_pct REAL, dwell_mean REAL,
            backend TEXT, updated_at TEXT DEFAULT (datetime('now'))
        )"""
    )
    conn.commit()
    report = walkforward_tune_lambda(
        conn,
        days=620,
        train_days=200,
        val_days=30,
        step_days=20,
        candidates=(5.0, 10.0, 15.0),
        backend="simple",
    )
    assert not report.get("error")
    assert report["summary"]["window_count"] >= 1
    assert len(report["timeline"]) >= 1

    n = persist_lambda_timeline(conn, report)
    assert n >= 1
    td = report["timeline"][0]["trade_date"]
    lam = get_jump_penalty_for_date(conn, td)
    assert lam in (5.0, 10.0, 15.0)
    conn.close()
