"""波动率 / 流动性预测测试。"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.volatility_forecast import (
    _compute_forecast,
    get_forecast_for_stock,
    get_summary_for_date,
    sync_forecast,
)


def _make_db() -> sqlite3.Connection:
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    conn = sqlite3.connect(f.name)
    conn.execute("CREATE TABLE stocks (id INTEGER PRIMARY KEY, is_active INT, code TEXT, name TEXT)")
    conn.execute("INSERT INTO stocks VALUES (1, 1, '000001', '测试股')")
    conn.execute(
        """CREATE TABLE stock_daily_quotes (
            stock_id INTEGER, trade_date TEXT, close REAL, volume REAL,
            high REAL, low REAL, turnover REAL, amount REAL,
            PRIMARY KEY (stock_id, trade_date))"""
    )
    conn.execute(
        """CREATE TABLE volatility_forecast_daily (
            stock_id INTEGER, trade_date TEXT, realized_vol_20 REAL, realized_vol_60 REAL,
            avg_turnover_20 REAL, avg_amount_20 REAL, amihud_illiq_20 REAL,
            forecast_vol_20 REAL, forecast_horizon INTEGER, forecast_method TEXT,
            PRIMARY KEY (stock_id, trade_date))"""
    )
    conn.commit()
    return conn


def _seed_quotes(conn: sqlite3.Connection, start: int = 1, end: int = 30) -> None:
    for i in range(start, end + 1):
        close = 10.0 + i * 0.1
        conn.execute(
            "INSERT INTO stock_daily_quotes VALUES (1, ?, ?, 1e6, 10.5, 9.5, 2.0, 1e7)",
            (f"2026-07-{i:02d}", close),
        )
    conn.commit()


def test_compute_forecast_basic():
    closes = [10.0 + i * 0.1 for i in range(30)]
    forecast = _compute_forecast(closes)
    assert "realized_vol_20" in forecast
    assert "forecast_vol_20" in forecast
    assert forecast["realized_vol_20"] > 0
    assert forecast["forecast_vol_20"] > 0


def test_compute_forecast_too_short():
    closes = [10.0 + i * 0.1 for i in range(5)]
    forecast = _compute_forecast(closes)
    assert not forecast


def test_sync_forecast():
    conn = _make_db()
    _seed_quotes(conn, 1, 30)
    summary = sync_forecast(conn, trade_date="2026-07-30")
    assert summary["records"] >= 1
    assert summary["avg_realized_vol_20"] > 0

    rows = get_forecast_for_stock(conn, 1)
    assert len(rows) >= 1
    assert rows[0]["forecast_vol_20"] > 0

    summary2 = get_summary_for_date(conn, trade_date="2026-07-30")
    assert summary2["total_records"] >= 1
    conn.close()


def test_sync_forecast_idempotent():
    conn = _make_db()
    _seed_quotes(conn, 1, 30)
    sync_forecast(conn, trade_date="2026-07-30")
    first = conn.execute("SELECT COUNT(*) FROM volatility_forecast_daily").fetchone()[0]
    sync_forecast(conn, trade_date="2026-07-30")
    second = conn.execute("SELECT COUNT(*) FROM volatility_forecast_daily").fetchone()[0]
    assert first == second
    conn.close()
