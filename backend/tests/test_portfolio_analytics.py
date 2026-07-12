"""portfolio_analytics 核心指标测试 — SEC-OPS P1-7"""
from __future__ import annotations

import sqlite3
import sys
import os
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def patch_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test.db")
    import config
    monkeypatch.setattr(config, "DB_PATH", db_file)

    import importlib
    import db_util
    import services.portfolio_svc as svc
    import services.portfolio_analytics as analytics
    importlib.reload(db_util)
    importlib.reload(svc)
    importlib.reload(analytics)

    monkeypatch.setattr("services.trade_pricing._fetch_realtime", lambda code: None)

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    svc._ensure_tables(conn)
    today = date.today().strftime("%Y-%m-%d")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL, name TEXT,
            is_active INTEGER DEFAULT 1, market TEXT DEFAULT 'SZ'
        );
        CREATE TABLE IF NOT EXISTS stock_daily_quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL, trade_date TEXT NOT NULL,
            open REAL, close REAL, high REAL, low REAL, volume REAL, change_pct REAL,
            UNIQUE(stock_id, trade_date)
        );
    """)
    for code, price in [("000001", 10.0), ("000002", 8.0)]:
        conn.execute("INSERT OR IGNORE INTO stocks (code, name, is_active) VALUES (?,?,1)", (code, f"股票{code}"))
        sid = conn.execute("SELECT id FROM stocks WHERE code=?", (code,)).fetchone()[0]
        conn.execute(
            "INSERT OR REPLACE INTO stock_daily_quotes "
            "(stock_id, trade_date, open, close, high, low, volume, change_pct) VALUES (?,?,?,?,?,?,?,?)",
            (sid, today, price, price, price * 1.02, price * 0.98, 1_000_000, 0.0),
        )
    conn.commit()
    conn.close()

    yield analytics, svc


@pytest.fixture
def portfolio_with_history(patch_db, tmp_path):
    """创建一个有快照历史的组合"""
    analytics, svc = patch_db
    import config
    pf = svc.create_portfolio("hist", initial_cash=100_000)
    pid = pf["id"]

    # 手动插入快照历史，模拟 30 天涨跌
    conn = sqlite3.connect(config.DB_PATH)
    values = [100_000, 103_000, 101_000, 98_000, 105_000, 108_000]
    for i, v in enumerate(values):
        d = (date.today() - timedelta(days=len(values) - i - 1)).strftime("%Y-%m-%d")
        conn.execute(
            "INSERT OR REPLACE INTO portfolio_snapshots (portfolio_id, snapshot_date, total_value) VALUES (?,?,?)",
            (pid, d, v),
        )
    conn.execute("UPDATE portfolios SET initial_cash=100000, cash=108000 WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    return pid, analytics, svc


# ── compute_metrics ───────────────────────────────────────────

def test_compute_metrics_returns_dict(patch_db):
    analytics, svc = patch_db
    pf = svc.create_portfolio("m1", initial_cash=50_000)
    result = analytics.compute_metrics(pf["id"])
    assert isinstance(result, dict)
    assert "total_return_pct" in result
    assert "max_drawdown_pct" in result


def test_compute_metrics_zero_return_on_fresh(patch_db):
    analytics, svc = patch_db
    pf = svc.create_portfolio("m2", initial_cash=50_000)
    result = analytics.compute_metrics(pf["id"])
    # 无交易无快照，收益率应为 0
    assert result["total_return_pct"] == 0.0


def test_compute_metrics_positive_return(portfolio_with_history):
    pid, analytics, svc = portfolio_with_history
    result = analytics.compute_metrics(pid)
    # 初始 100k → 108k，总收益 8%
    assert result["total_return_pct"] == pytest.approx(8.0, rel=0.05)


def test_compute_metrics_max_drawdown(portfolio_with_history):
    pid, analytics, svc = portfolio_with_history
    result = analytics.compute_metrics(pid)
    # 峰值 108k 之前曾跌到 98k（峰值 105k 时），回撤约 6.67%
    assert result["max_drawdown_pct"] > 0


def test_compute_metrics_invalid_portfolio(patch_db):
    analytics, svc = patch_db
    result = analytics.compute_metrics(99999)
    assert "error" in result


# ── _journal_stats ────────────────────────────────────────────

def test_journal_stats_empty():
    import importlib, sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from services.portfolio_analytics import _journal_stats
    stats = _journal_stats([])
    assert stats["win_rate_pct"] == 0
    assert stats["closed_trades"] == 0
    assert stats["realized_pnl"] == 0


def test_journal_stats_buy_only():
    from services.portfolio_analytics import _journal_stats
    journal = [
        {"action": "buy", "code": "000001", "shares": 100, "price": 10.0,
         "commission": 3.0, "tax": 0.0, "trade_date": "2024-01-01"},
    ]
    stats = _journal_stats(journal)
    # 只有买入，无已实现盈亏
    assert stats["closed_trades"] == 0


def test_journal_stats_round_trip_profit():
    from services.portfolio_analytics import _journal_stats
    journal = [
        {"action": "buy",  "code": "000001", "shares": 100, "price": 10.0,
         "commission": 3.0, "tax": 0.0, "trade_date": "2024-01-01"},
        {"action": "sell", "code": "000001", "shares": 100, "price": 12.0,
         "commission": 3.6, "tax": 1.2, "trade_date": "2024-01-10"},
    ]
    stats = _journal_stats(journal)
    assert stats["closed_trades"] == 1
    assert stats["realized_pnl"] > 0  # 卖出价 > 买入价
    assert stats["win_rate_pct"] == 100.0


# ── estimate_trade_fees ───────────────────────────────────────

def test_estimate_trade_fees_buy(patch_db):
    analytics, _ = patch_db
    result = analytics.estimate_trade_fees("000001", "buy", 100)
    assert "commission" in result
    assert result["commission"] >= 0
