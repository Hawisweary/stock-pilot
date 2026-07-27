"""市场状态分类测试。"""
import os
import sqlite3
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.market_regime import (
    classify_regime,
    compute_market_features,
    describe_regime_weight_deltas,
    get_regime_guidance,
    regime_label,
)


def _kline(n: int = 120, base: float = 100.0, regime: str = "oscillation") -> list[dict]:
    bars = []
    px = base
    for i in range(n):
        d = (date(2026, 7, 1) + timedelta(days=i)).isoformat()
        if regime == "strong_trend_up":
            px *= 1.005
        elif regime == "strong_trend_down":
            px *= 0.995
        elif regime == "high_volatility":
            px *= 1.0 + 0.04 * (1 if i % 2 == 0 else -1)
        else:
            px *= 1 + 0.002 * (1 if i % 5 < 3 else -1)
        high = px * 1.02
        low = px * 0.98
        bars.append({"date": d, "open": px, "high": high, "low": low, "close": px, "volume": 1e8})
    return bars


def test_classify_trend_up():
    kline = _kline(120, regime="strong_trend_up")
    result = classify_regime(kline)
    assert result["regime"] == "strong_trend_up"
    assert result["regime_label"] == "趋势上涨"
    assert result["return_20d"] > 0.05
    assert result["return_60d"] > 0.05


def test_classify_trend_down():
    kline = _kline(120, regime="strong_trend_down")
    result = classify_regime(kline)
    assert result["regime"] == "strong_trend_down"
    assert result["regime_label"] == "趋势下跌"


def test_classify_high_volatility():
    kline = _kline(120, regime="high_volatility")
    result = classify_regime(kline)
    assert result["regime"] == "high_volatility"
    assert result["regime_label"] == "高波动"
    assert result["volatility_20"] > 0.30


def test_classify_liquidity_drought_with_features():
    kline = _kline(120, regime="oscillation")
    result = classify_regime(
        kline,
        features={
            "ad_ratio": 0.48,
            "amount_ratio_20": 0.50,
            "rotation_speed": 0.4,
            "avg_corr_20": 0.35,
            "liquidity_score": 0.50,
        },
    )
    assert result["regime"] == "liquidity_drought"
    assert result["regime_label"] == "流动性枯竭"


def test_regime_label_mapping():
    assert regime_label("weak_trend_up") == "趋势上涨"
    assert regime_label("oscillation") == "震荡"


def test_regime_guidance():
    g = get_regime_guidance("high_volatility")
    assert g["max_position"] == 0.40
    assert g["regime_label"] == "高波动"
    assert "波动" in g["note"]


def test_describe_weight_deltas():
    note = describe_regime_weight_deltas("strong_trend_down")
    assert "质量" in note
    assert "估值" in note
    assert describe_regime_weight_deltas("oscillation") == "权重保持基线，无额外调整"


def _make_quotes_db() -> sqlite3.Connection:
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    conn = sqlite3.connect(f.name)
    conn.execute("CREATE TABLE stocks (id INTEGER PRIMARY KEY, industry_sw TEXT, is_active INT)")
    conn.execute(
        """CREATE TABLE stock_daily_quotes (
            stock_id INTEGER, trade_date TEXT, close REAL, amount REAL,
            PRIMARY KEY (stock_id, trade_date))"""
    )
    conn.execute("INSERT INTO stocks VALUES (1, '银行', 1), (2, '银行', 1), (3, '医药', 1)")
    dates = [(date(2026, 7, 1) + timedelta(days=i)).isoformat() for i in range(15)]
    for i, d in enumerate(dates):
        conn.execute("INSERT INTO stock_daily_quotes VALUES (1, ?, ?, 1e9)", (d, 10 + i * 0.1))
        conn.execute("INSERT INTO stock_daily_quotes VALUES (2, ?, ?, 1e9)", (d, 10 - i * 0.05))
        conn.execute("INSERT INTO stock_daily_quotes VALUES (3, ?, ?, 5e8)", (d, 20 + i * 0.2))
    conn.commit()
    return conn


def test_compute_market_features():
    conn = _make_quotes_db()
    trade_date = (date(2026, 7, 1) + timedelta(days=14)).isoformat()
    feats = compute_market_features(conn, trade_date)
    assert feats["ad_ratio"] is not None
    assert 0 <= feats["ad_ratio"] <= 1
    assert feats["amount_ratio_20"] is not None
    conn.close()
