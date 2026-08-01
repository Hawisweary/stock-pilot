"""市场状态验证框架单元测试（无网络、无全量回测）。"""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.market_regime import classify_regime, regime_bucket
from services.regime_validation import (
    _f_statistic,
    compute_dwell_times,
    index_returns_from_kline,
    internal_consistency_report,
    permutation_anova_pvalue,
    walk_forward_report,
)


def _kline(n: int = 120, regime: str = "oscillation") -> list[dict]:
    bars = []
    px = 100.0
    for i in range(n):
        d = (date(2026, 1, 1) + timedelta(days=i)).isoformat()
        if regime == "strong_trend_up":
            px *= 1.004
        elif regime == "strong_trend_down":
            px *= 0.996
        elif regime == "high_volatility":
            px *= 1.0 + 0.03 * (1 if i % 2 == 0 else -1)
        else:
            px *= 1 + 0.001 * (1 if i % 4 < 2 else -1)
        bars.append({"date": d, "open": px, "high": px * 1.01, "low": px * 0.99, "close": px})
    return bars


def test_index_returns_from_kline():
    k = _kline(10)
    rets = index_returns_from_kline(k)
    assert len(rets) == 9
    assert all(abs(r) < 0.1 for r in rets.values())


def test_dwell_times():
    buckets = ["trend_up", "trend_up", "oscillation", "oscillation", "oscillation", "trend_down"]
    d = compute_dwell_times(buckets)
    assert d["overall_mean_days"] == 2.0
    assert d["by_bucket"]["trend_up"]["mean_dwell_days"] == 2.0
    assert d["by_bucket"]["oscillation"]["mean_dwell_days"] == 3.0


def test_f_statistic_and_permutation():
    groups = [
        [0.02, 0.025, 0.022, 0.021, 0.019, 0.024],
        [-0.02, -0.018, -0.025, -0.021, -0.019, -0.023],
    ]
    assert _f_statistic(groups) > 50.0
    p = permutation_anova_pvalue(groups, n_perm=999, seed=7)
    assert p <= 0.1


def test_internal_consistency_synthetic():
    k = _kline(200, regime="strong_trend_up")
    rets = index_returns_from_kline(k)
    rows = []
    for i in range(65, len(k)):
        sub = k[: i + 1]
        r = classify_regime(sub)
        rows.append({
            "trade_date": sub[-1]["date"],
            "bucket": regime_bucket(r["regime"], float(r.get("price_vs_ma60") or 0)),
            "volatility_20": r.get("volatility_20"),
        })
    report = internal_consistency_report(rows, rets)
    assert report["sample_days"] == len(rows)
    assert "return_anova" in report
    assert report["dwell_time"]["overall_mean_days"] >= 1


def test_walk_forward_runs():
    k = _kline(150)
    wf = walk_forward_report(k)
    assert wf["sample_days"] > 0
    assert wf["bucket_match_rate_pct"] is not None
    assert wf["persistence_baseline_pct"] is not None
