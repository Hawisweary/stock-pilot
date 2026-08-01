"""P3-E Jump Model 测试。"""
import os
import sys
from datetime import date, timedelta

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.regime_jump import (
    SimpleJumpModel,
    _viterbi_jump,
    fit_jump,
    predict_jump_buckets,
    jumpmodels_available,
)


def test_viterbi_jump_penalty_increases_dwell():
    dist = np.random.default_rng(1).random((40, 3))
    low = _viterbi_jump(dist, jump_penalty=0.1)
    high = _viterbi_jump(dist, jump_penalty=100.0)
    ch_low = sum(low[i] != low[i - 1] for i in range(1, len(low)))
    ch_high = sum(high[i] != high[i - 1] for i in range(1, len(high)))
    assert ch_high <= ch_low


def test_simple_jump_model_synthetic():
    rng = np.random.default_rng(42)
    chunks = []
    for mean in [
        [0.08, 0.10, 35, 0.002, 0.05],
        [-0.06, 0.24, 38, -0.001, -0.04],
        [0.0, 0.09, 20, 0.0, 0.0],
        [0.01, 0.20, 42, 0.001, 0.01],
    ]:
        chunks.append(rng.normal(mean, 0.02, size=(45, 5)))
    X = np.vstack(chunks)
    dates = [(date(2024, 1, 1) + timedelta(days=i)).isoformat() for i in range(len(X))]
    fit = fit_jump(X, jump_penalty=50.0, backend="simple")
    rows = predict_jump_buckets(fit, dates, X)
    buckets = {r["regime_bucket"] for r in rows}
    assert len(buckets) >= 2
    assert fit.backend == "simple_dp"


@pytest.mark.skipif(not jumpmodels_available(), reason="jumpmodels 需 Python≥3.10")
def test_jumpmodels_backend_if_available():
    rng = np.random.default_rng(7)
    X = rng.normal(0, 0.05, size=(120, 5))
    dates = [(date(2024, 6, 1) + timedelta(days=i)).isoformat() for i in range(len(X))]
    fit = fit_jump(X, jump_penalty=25.0, backend="jumpmodels")
    assert fit.backend == "jumpmodels"
    rows = predict_jump_buckets(fit, dates, X)
    assert len(rows) == len(dates)


def test_simple_jump_class_direct():
    X = np.random.default_rng(0).normal(size=(50, 5))
    m = SimpleJumpModel(n_components=3, jump_penalty=10.0)
    m.fit(X)
    pred = m.predict(X)
    assert len(pred) == 50
