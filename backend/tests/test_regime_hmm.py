"""P3-C HMM regime 测试。"""
import os
import sqlite3
import sys
from datetime import date, timedelta

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.regime_hmm import (
    map_states_to_buckets,
    fit_hmm,
    predict_buckets,
    load_hmm_features,
)


def test_map_states_to_buckets():
    stats = [
        {"state": 0, "count": 50, "ret20": -0.05, "vol20": 0.25, "adx": 30, "pv_ma60": -0.04},
        {"state": 1, "count": 100, "ret20": 0.08, "vol20": 0.12, "adx": 35, "pv_ma60": 0.06},
        {"state": 2, "count": 80, "ret20": 0.01, "vol20": 0.22, "adx": 40, "pv_ma60": 0.0},
        {"state": 3, "count": 120, "ret20": 0.0, "vol20": 0.08, "adx": 18, "pv_ma60": 0.01},
    ]
    m = map_states_to_buckets(stats)
    assert m[1] == "trend_up"
    assert m[0] == "trend_down"
    assert m[2] == "high_vol"
    assert m[3] == "oscillation"


def test_fit_hmm_synthetic():
    pytest.importorskip("hmmlearn")
    rng = np.random.default_rng(42)
    chunks = []
    for mean in [
        [0.08, 0.10, 35, 0.002, 0.05],
        [-0.06, 0.24, 38, -0.001, -0.04],
        [0.0, 0.09, 20, 0.0, 0.0],
        [0.01, 0.20, 42, 0.001, 0.01],
    ]:
        chunks.append(rng.normal(mean, 0.02, size=(40, 5)))
    X = np.vstack(chunks)
    dates = [(date(2024, 1, 1) + timedelta(days=i)).isoformat() for i in range(len(X))]
    fit = fit_hmm(X, n_iter=30)
    rows = predict_buckets(fit, dates, X)
    buckets = {r["regime_bucket"] for r in rows}
    assert len(buckets) >= 2


def test_load_hmm_features_empty_db():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE market_regime_daily (
            trade_date TEXT PRIMARY KEY,
            return_20d REAL, return_20d_csi800 REAL,
            volatility_20 REAL, volatility_20_csi800 REAL,
            adx REAL, adx_csi800 REAL,
            ma20_slope REAL, ma20_slope_csi800 REAL,
            price_vs_ma60 REAL, price_vs_ma60_csi800 REAL
        )"""
    )
    dates, X = load_hmm_features(conn, days=100)
    assert dates == []
    assert X.size == 0
    conn.close()
