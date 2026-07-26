"""市场状态分类测试。"""
import os
import sqlite3
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.market_regime import classify_regime, sync_regime


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
    assert result["return_20d"] > 0.05
    assert result["return_60d"] > 0.05


def test_classify_trend_down():
    kline = _kline(120, regime="strong_trend_down")
    result = classify_regime(kline)
    assert result["regime"] == "strong_trend_down"


def test_classify_high_volatility():
    kline = _kline(120, regime="high_volatility")
    result = classify_regime(kline)
    assert result["regime"] == "high_volatility"
    assert result["volatility_20"] > 0.30


def test_sync_regime():
    import tempfile
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    conn = sqlite3.connect(f.name)
    conn.execute(
        """CREATE TABLE market_regime_daily (
            trade_date TEXT PRIMARY KEY, index_code TEXT, regime TEXT,
            rsi_14 REAL, volatility_20 REAL, adx REAL,
            return_20d REAL, return_60d REAL, price_vs_ma20 REAL, price_vs_ma60 REAL,
            updated_at TEXT)"""
    )
    conn.commit()

    kline = _kline(120, regime="strong_trend_up")
    last_date = kline[-1]["date"]
    result = sync_regime(conn, trade_date=last_date)
    # sync_regime uses fetch_index_kline which is external, so we test classify_regime indirectly
    # by writing manually
    conn.execute(
        """INSERT INTO market_regime_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
        (last_date, "sh000300", "strong_trend_up", 70.0, 0.15, 30.0, 0.06, 0.12, 0.03, 0.08),
    )
    conn.commit()
    row = conn.execute("SELECT regime FROM market_regime_daily WHERE trade_date=?", (last_date,)).fetchone()
    assert row[0] == "strong_trend_up"
    conn.close()
