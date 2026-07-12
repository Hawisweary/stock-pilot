"""技术面五档规则引擎测试。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.technical_rule_engine import (
    ENGINE_ID,
    _subtotal_to_tier,
    compute_technical_tier,
    fuse_technical_tiers,
    preprocess_ohlcv,
    score_trend_module,
)


def _make_ohlcv(n: int = 80, *, trend: float = 0.002, vol: float = 1e6) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    close = 10.0 * np.cumprod(1 + np.full(n, trend))
    high = close * 1.01
    low = close * 0.99
    open_ = close * 0.998
    volume = np.full(n, vol)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )


def test_preprocess_ffill():
    df = _make_ohlcv(30)
    df.loc[df.index[5], "volume"] = np.nan
    out = preprocess_ohlcv(df)
    assert out["volume"].isna().sum() == 0


def test_subtotal_mapping():
    assert _subtotal_to_tier(2.5) == 2
    assert _subtotal_to_tier(1.2) == 1
    assert _subtotal_to_tier(0.0) == 0
    assert _subtotal_to_tier(-1.2) == -1
    assert _subtotal_to_tier(-2.5) == -2


def test_fuse_extreme_bear():
    assert fuse_technical_tiers(-2, -1, 0) == -2
    assert fuse_technical_tiers(-2, -2, 1) == -2


def test_fuse_extreme_bull():
    assert fuse_technical_tiers(2, 1, 0) == 2
    assert fuse_technical_tiers(2, 2, -1) == 2


def test_uptrend_positive_tier():
    df = _make_ohlcv(80, trend=0.008)
    r = compute_technical_tier(df)
    assert r["engine"] == ENGINE_ID
    assert -2 <= r["final_technical_tier"] <= 2
    assert r["trend_tier"] >= 0


def test_insufficient_bars_neutral():
    df = _make_ohlcv(10)
    r = compute_technical_tier(df)
    assert r["final_technical_tier"] == 0


def test_score_matches_tier_pct():
    df = _make_ohlcv(80)
    r = compute_technical_tier(df)
    assert r["score"] == (r["final_technical_tier"] + 2) * 25
