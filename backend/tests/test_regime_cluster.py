"""P3-D K-Means / GMM regime 测试。"""
import os
import sys
from datetime import date, timedelta

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.regime_cluster import (
    fit_cluster,
    predict_cluster_buckets,
    _map_clusters_to_buckets,
    _cluster_feature_stats,
)


def test_map_clusters_to_buckets():
    stats = [
        {"cluster": 0, "count": 10, "ret20": 0.1, "vol20": 0.15, "adx": 30, "pv_ma60": 0.05},
        {"cluster": 1, "count": 10, "ret20": -0.08, "vol20": 0.2, "adx": 35, "pv_ma60": -0.04},
        {"cluster": 2, "count": 10, "ret20": 0.02, "vol20": 0.35, "adx": 40, "pv_ma60": 0.0},
        {"cluster": 3, "count": 10, "ret20": 0.0, "vol20": 0.12, "adx": 20, "pv_ma60": 0.01},
    ]
    m = _map_clusters_to_buckets(stats)
    assert len(set(m.values())) >= 3


def test_fit_kmeans_synthetic():
    pytest.importorskip("sklearn")
    rng = np.random.default_rng(7)
    chunks = []
    for mean in [
        [0.08, 0.10, 35, 0.002, 0.05],
        [-0.06, 0.24, 38, -0.001, -0.04],
        [0.0, 0.09, 20, 0.0, 0.0],
        [0.01, 0.20, 42, 0.001, 0.01],
    ]:
        chunks.append(rng.normal(mean, 0.02, size=(50, 5)))
    X = np.vstack(chunks)
    dates = [(date(2024, 1, 1) + timedelta(days=i)).isoformat() for i in range(len(X))]
    fit = fit_cluster(X, method="kmeans")
    rows = predict_cluster_buckets(fit, dates, X)
    buckets = {r["regime_bucket"] for r in rows}
    assert len(buckets) >= 2
    assert fit.method == "kmeans"


def test_fit_gmm_synthetic():
    pytest.importorskip("sklearn")
    rng = np.random.default_rng(11)
    X = rng.normal([0.02, 0.15, 30, 0.0, 0.0], 0.03, size=(120, 5))
    dates = [(date(2024, 3, 1) + timedelta(days=i)).isoformat() for i in range(len(X))]
    fit = fit_cluster(X, method="gmm")
    rows = predict_cluster_buckets(fit, dates, X)
    assert len(rows) == len(dates)
    stats = _cluster_feature_stats(X, fit.model.predict(
        (X - np.asarray(fit.feature_means)) / np.asarray(fit.feature_stds)
    ))
    assert sum(s["count"] for s in stats) == len(X)
