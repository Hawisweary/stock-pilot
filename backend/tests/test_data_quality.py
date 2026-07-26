"""数据质量 / 异常检测 Phase 1 + Phase 3 测试。"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.data_quality import AnomalyDetector, detect_and_write, get_alerts_for_stock


def _make_db() -> sqlite3.Connection:
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    conn = sqlite3.connect(f.name)
    conn.execute(
        """CREATE TABLE stocks (id INTEGER PRIMARY KEY, industry_sw2 TEXT, industry_sw TEXT, is_active INT, list_date TEXT)"""
    )
    conn.execute("INSERT INTO stocks VALUES (1, '银行', '', 1, '2020-01-01')")
    conn.execute("INSERT INTO stocks VALUES (2, '银行', '', 1, '2020-01-01')")
    conn.execute(
        """CREATE TABLE stock_daily_quotes (
            stock_id INTEGER, trade_date TEXT, close REAL, volume REAL,
            high REAL, low REAL, turnover REAL, amount REAL, is_suspended INTEGER DEFAULT 0,
            PRIMARY KEY (stock_id, trade_date))"""
    )
    conn.execute(
        """CREATE TABLE stock_v5_metrics (
            stock_id INTEGER, calc_date TEXT, revenue_yoy_q REAL, cfo_np REAL,
            debt_ratio REAL, quality_tier REAL,
            PRIMARY KEY (stock_id, calc_date))"""
    )
    conn.execute(
        """CREATE TABLE valuation_snapshots (
            stock_id INTEGER, as_of_date TEXT, pe_ttm REAL, pb REAL, dividend_yield REAL,
            PRIMARY KEY (stock_id, as_of_date))"""
    )
    conn.execute(
        """CREATE TABLE stock_fund_flow_daily (
            stock_id INTEGER, trade_date TEXT, main_net_5d REAL,
            PRIMARY KEY (stock_id, trade_date))"""
    )
    conn.execute(
        """CREATE TABLE data_quality_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, stock_id INTEGER, trade_date TEXT,
            anomaly_score REAL, flags TEXT, severity TEXT, isolation_score REAL,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(stock_id, trade_date))"""
    )
    conn.execute(
        """CREATE TABLE stock_ex_rights (
            id INTEGER PRIMARY KEY AUTOINCREMENT, stock_id INTEGER, ex_date TEXT,
            cash_div REAL, bonus_ratio REAL, transfer_ratio REAL,
            UNIQUE(stock_id, ex_date))"""
    )
    conn.execute(
        """CREATE TABLE stock_lifecycle (
            stock_id INTEGER PRIMARY KEY, code TEXT, list_date TEXT, delist_date TEXT)"""
    )
    conn.commit()
    return conn


def test_price_spike():
    conn = _make_db()
    # Stock 1: 前 10 天正常，今天涨 16%
    for i in range(10, 0, -1):
        d = f"2026-07-{i+10:02d}"
        conn.execute(
            "INSERT INTO stock_daily_quotes (stock_id, trade_date, close, volume, high, low, turnover, amount) VALUES (1, ?, 10.0, 1e6, 10.5, 9.5, 2.0, 1e7)",
            (d,),
        )
    conn.execute(
        "INSERT INTO stock_daily_quotes (stock_id, trade_date, close, volume, high, low, turnover, amount) VALUES (1, '2026-07-25', 11.6, 1e6, 12.0, 11.0, 2.0, 1e7)",
    )
    conn.execute("INSERT INTO stock_ex_rights (stock_id, ex_date) VALUES (1, '2026-07-25')")
    conn.execute("INSERT INTO valuation_snapshots (stock_id, as_of_date, pe_ttm, pb) VALUES (1, '2026-07-25', 15.0, 1.5)")
    conn.commit()

    det = AnomalyDetector(conn, trade_date="2026-07-25", lookback=30)
    alerts = [a for a in det.detect() if a["stock_id"] == 1]
    assert len(alerts) >= 1
    assert "price_spike" in alerts[0]["flags"]
    assert alerts[0]["anomaly_score"] > 0
    conn.close()


def test_volume_burst():
    conn = _make_db()
    # Stock 1: 20 天成交量 1e6，今天 20e6
    for i in range(1, 21):
        d = f"2026-07-{i:02d}"
        conn.execute(
            "INSERT INTO stock_daily_quotes (stock_id, trade_date, close, volume, high, low, turnover, amount) VALUES (1, ?, 10.0, 1e6, 10.5, 9.5, 2.0, 1e7)",
            (d,),
        )
    conn.execute(
        "INSERT INTO stock_daily_quotes (stock_id, trade_date, close, volume, high, low, turnover, amount) VALUES (1, '2026-07-25', 10.0, 20e6, 10.5, 9.5, 2.0, 2e8)",
    )
    conn.commit()

    det = AnomalyDetector(conn, trade_date="2026-07-25", lookback=30)
    alerts = det.detect()
    assert any("volume_burst" in a["flags"] for a in alerts)
    conn.close()


def test_pe_extreme():
    conn = _make_db()
    conn.execute(
        "INSERT INTO stock_daily_quotes (stock_id, trade_date, close, volume, high, low, turnover, amount) VALUES (1, '2026-07-25', 10.0, 1e6, 10.5, 9.5, 2.0, 1e7)"
    )
    conn.execute(
        "INSERT INTO stock_daily_quotes (stock_id, trade_date, close, volume, high, low, turnover, amount) VALUES (1, '2026-07-24', 10.0, 1e6, 10.5, 9.5, 2.0, 1e7)"
    )
    conn.execute("INSERT INTO valuation_snapshots VALUES (1, '2026-07-25', -1000, 1.0, 3.0)")
    conn.commit()

    det = AnomalyDetector(conn, trade_date="2026-07-25", lookback=30)
    alerts = det.detect()
    assert any("pe_extreme" in a["flags"] for a in alerts)
    conn.close()


def test_detect_and_write():
    conn = _make_db()
    conn.execute(
        "INSERT INTO stock_daily_quotes (stock_id, trade_date, close, volume, high, low, turnover, amount) VALUES (1, '2026-07-25', 11.6, 1e6, 12.0, 11.0, 2.0, 1e7)"
    )
    conn.execute(
        "INSERT INTO stock_daily_quotes (stock_id, trade_date, close, volume, high, low, turnover, amount) VALUES (1, '2026-07-24', 10.0, 1e6, 10.5, 9.5, 2.0, 1e7)"
    )
    conn.execute("INSERT INTO valuation_snapshots VALUES (1, '2026-07-25', -1000, 1.0, 3.0)")
    conn.commit()

    summary = detect_and_write(conn, trade_date="2026-07-25")
    assert summary["total_alerts"] >= 1
    assert summary["trade_date"] == "2026-07-25"

    alerts = get_alerts_for_stock(conn, 1)
    assert len(alerts) >= 1
    conn.close()


def test_ex_rights_mismatch():
    conn = _make_db()
    # 涨 16% 却无除权记录
    conn.execute(
        "INSERT INTO stock_daily_quotes (stock_id, trade_date, close, volume, high, low, turnover, amount) VALUES (1, '2026-07-24', 10.0, 1e6, 10.5, 9.5, 2.0, 1e7)"
    )
    conn.execute(
        "INSERT INTO stock_daily_quotes (stock_id, trade_date, close, volume, high, low, turnover, amount) VALUES (1, '2026-07-25', 11.6, 1e6, 12.0, 11.0, 2.0, 1e7)"
    )
    conn.commit()

    det = AnomalyDetector(conn, trade_date="2026-07-25", lookback=30)
    alerts = det.detect()
    assert any("ex_rights_mismatch" in a["flags"] for a in alerts)
    conn.close()


def test_ex_rights_matched():
    conn = _make_db()
    # 涨 16% 但有除权记录，不应 flagged
    conn.execute(
        "INSERT INTO stock_daily_quotes (stock_id, trade_date, close, volume, high, low, turnover, amount) VALUES (1, '2026-07-24', 10.0, 1e6, 10.5, 9.5, 2.0, 1e7)"
    )
    conn.execute(
        "INSERT INTO stock_daily_quotes (stock_id, trade_date, close, volume, high, low, turnover, amount) VALUES (1, '2026-07-25', 11.6, 1e6, 12.0, 11.0, 2.0, 1e7)"
    )
    conn.execute("INSERT INTO stock_ex_rights (stock_id, ex_date) VALUES (1, '2026-07-25')")
    conn.commit()

    det = AnomalyDetector(conn, trade_date="2026-07-25", lookback=30)
    alerts = det.detect()
    assert not any("ex_rights_mismatch" in a["flags"] for a in alerts)
    conn.close()


def test_suspended_with_trades():
    conn = _make_db()
    conn.execute(
        "INSERT INTO stock_daily_quotes (stock_id, trade_date, close, volume, high, low, turnover, amount, is_suspended) VALUES (1, '2026-07-25', 10.0, 1e6, 10.5, 9.5, 2.0, 1e7, 1)"
    )
    conn.commit()

    det = AnomalyDetector(conn, trade_date="2026-07-25", lookback=30)
    alerts = det.detect()
    assert any("suspended_with_trades" in a["flags"] for a in alerts)
    conn.close()


def test_valuation_outdated():
    conn = _make_db()
    conn.execute(
        "INSERT INTO stock_daily_quotes (stock_id, trade_date, close, volume, high, low, turnover, amount) VALUES (1, '2026-07-25', 10.0, 1e6, 10.5, 9.5, 2.0, 1e7)"
    )
    conn.execute("INSERT INTO valuation_snapshots (stock_id, as_of_date, pe_ttm, pb) VALUES (1, '2026-06-01', 15.0, 1.5)")
    conn.commit()

    det = AnomalyDetector(conn, trade_date="2026-07-25", lookback=30)
    alerts = det.detect()
    assert any("valuation_outdated" in a["flags"] for a in alerts)
    conn.close()


def test_newly_listed():
    conn = _make_db()
    conn.execute(
        "INSERT INTO stock_daily_quotes (stock_id, trade_date, close, volume, high, low, turnover, amount) VALUES (1, '2026-07-25', 10.0, 1e6, 10.5, 9.5, 2.0, 1e7)"
    )
    conn.execute("INSERT INTO stock_lifecycle (stock_id, code, list_date) VALUES (1, '000001', '2026-06-01')")
    conn.commit()

    det = AnomalyDetector(conn, trade_date="2026-07-25", lookback=30)
    alerts = det.detect()
    assert any("newly_listed" in a["flags"] for a in alerts)
    conn.close()
