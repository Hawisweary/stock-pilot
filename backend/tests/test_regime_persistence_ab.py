"""P3-A persistence A/B 测试。"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.regime_persistence_ab import (
    PersistenceVariant,
    confirmed_buckets_for_variant,
    load_raw_regime_series,
    score_variant,
    variant_by_id,
)


def test_symmetric_5_absorbs_short_trend_down():
    series = {
        "dates": [f"2025-01-0{d}" for d in range(1, 8)],
        "raw800": ["strong_trend_down"] * 2 + ["oscillation"] * 5,
        "pv800": [-0.05] * 7,
    }
    variant = PersistenceVariant(
        id="symmetric_5", label="sym5", asymmetric=False, symmetric_days=5,
    )
    buckets = confirmed_buckets_for_variant(series, variant)
    assert buckets.count("trend_down") == 0


def test_asymmetric_keeps_short_trend_down():
    series = {
        "dates": [f"2025-01-0{d}" for d in range(1, 8)],
        "raw800": ["strong_trend_down"] * 2 + ["oscillation"] * 5,
        "pv800": [-0.05] * 7,
    }
    variant = PersistenceVariant(
        id="asymmetric", label="asym", asymmetric=True, down_days=2,
    )
    buckets = confirmed_buckets_for_variant(series, variant)
    assert buckets.count("trend_down") == 2


def test_score_prefers_adequate_trend_down_sample():
    low = {
        "distribution": {"trend_down": 3, "trend_up": 20, "high_vol": 100, "oscillation": 400},
        "dwell_time": {"overall_mean_days": 20},
        "internal_consistency": {"return_anova_significant": True},
        "l3_simulation": {"strategy_switches": 5, "sharpe_lift_vs_composite": -0.2},
        "bucket_transitions": 30,
    }
    high = {**low, "distribution": {**low["distribution"], "trend_down": 14}}
    assert score_variant(high) > score_variant(low)


def test_variant_by_id():
    assert variant_by_id("symmetric_5") is not None
    assert variant_by_id("symmetric_3") is not None


def test_load_raw_empty_db():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    conn = sqlite3.connect(f.name)
    conn.executescript("""
        CREATE TABLE market_regime_daily (
            trade_date TEXT PRIMARY KEY,
            regime TEXT, regime_csi800 TEXT,
            regime_raw TEXT, regime_csi800_raw TEXT,
            regime_bucket_csi800 TEXT, regime_bucket_csi800_raw TEXT,
            price_vs_ma60 REAL, price_vs_ma60_csi800 REAL,
            volatility_20 REAL, volatility_20_csi800 REAL
        );
    """)
    out = load_raw_regime_series(conn, days=30)
    assert out.get("error")
    conn.close()
