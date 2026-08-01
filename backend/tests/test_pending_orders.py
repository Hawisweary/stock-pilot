"""待执行订单：收盘入队、次日开盘价成交。"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def patch_db(tmp_path, monkeypatch):
    monkeypatch.setattr("services.trade_pricing._fetch_realtime", lambda code: None)
    db_file = str(tmp_path / "test.db")
    import config

    monkeypatch.setattr(config, "DB_PATH", db_file)

    import importlib
    import db_util
    import services.portfolio_svc as svc
    import services.trade_pricing as tp

    importlib.reload(db_util)
    importlib.reload(tp)
    importlib.reload(svc)

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    svc._ensure_tables(conn)
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            name TEXT,
            is_active INTEGER DEFAULT 1,
            market TEXT DEFAULT 'SZ'
        );
        CREATE TABLE IF NOT EXISTS stock_daily_quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            trade_date TEXT NOT NULL,
            open REAL, close REAL, high REAL, low REAL,
            volume REAL, change_pct REAL,
            UNIQUE(stock_id, trade_date)
        );
        CREATE TABLE IF NOT EXISTS trade_calendar (
            cal_date TEXT PRIMARY KEY,
            is_open INTEGER NOT NULL
        );
    """)
    for d in [today, tomorrow]:
        conn.execute(
            "INSERT OR REPLACE INTO trade_calendar (cal_date, is_open) VALUES (?,1)",
            (d,),
        )
    for code, open_px, close_px in [("000001", 9.8, 10.0), ("000002", 4.9, 5.0)]:
        conn.execute(
            "INSERT OR IGNORE INTO stocks (code, name, is_active) VALUES (?,?,1)",
            (code, f"股票{code}"),
        )
        sid = conn.execute("SELECT id FROM stocks WHERE code=?", (code,)).fetchone()[0]
        conn.execute(
            "INSERT OR REPLACE INTO stock_daily_quotes "
            "(stock_id, trade_date, open, close, high, low, volume, change_pct) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (sid, today, open_px, close_px, close_px * 1.02, close_px * 0.98, 1_000_000, 0.0),
        )
        conn.execute(
            "INSERT OR REPLACE INTO stock_daily_quotes "
            "(stock_id, trade_date, open, close, high, low, volume, change_pct) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (sid, tomorrow, open_px + 0.1, close_px + 0.1, close_px * 1.02, close_px * 0.98, 1_000_000, 0.0),
        )
    conn.commit()
    conn.close()

    from services.trade_calendar import invalidate_cache
    invalidate_cache()
    yield svc


@pytest.fixture
def svc(patch_db):
    return patch_db


def test_open_price_mode_uses_open_not_close(svc, monkeypatch):
    import config
    import services.trade_pricing as tp

    conn = sqlite3.connect(config.DB_PATH)
    sid = conn.execute("SELECT id FROM stocks WHERE code='000001'").fetchone()[0]
    conn.close()
    today = date.today().isoformat()

    monkeypatch.setattr(
        tp,
        "get_market_context",
        lambda conn=None: tp.MarketContext(
            tp._now_cn(), today, today, "intraday", True, None, "盘中",
        ),
    )
    q = tp.resolve_trade_price(
        "000001", sid, "buy", price_mode="open", as_of_trade_date=today,
    )
    assert q.error is None
    assert q.source == "open"
    assert q.raw_price == pytest.approx(9.8)


def test_queue_rebalance_and_execute_at_open(svc, monkeypatch):
    pf = svc.create_portfolio("rb", initial_cash=200_000)
    pid = pf["id"]
    svc.trade(pid, "000001", "buy", 100)
    exec_date = svc._exec_date_after_signal()

    import config

    conn = sqlite3.connect(config.DB_PATH)
    sid = conn.execute("SELECT id FROM stocks WHERE code='000001'").fetchone()[0]
    for i in range(1, 7):
        d = (date.today() - timedelta(days=i)).isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO trade_calendar (cal_date, is_open) VALUES (?,1)",
            (d,),
        )
        conn.execute(
            "INSERT OR REPLACE INTO stock_daily_quotes "
            "(stock_id, trade_date, open, close, high, low, volume, change_pct) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (sid, d, 10.0, 10.0, 10.2, 9.8, 1_000_000, 0.0),
        )
    conn.execute(
        "UPDATE portfolios SET rebalance_schedule='weekly', last_rebalance_date=? WHERE id=?",
        ((date.today() - timedelta(days=10)).isoformat(), pid),
    )
    conn.commit()
    conn.close()

    from services.trade_calendar import invalidate_cache
    invalidate_cache()

    queued = svc.queue_scheduled_rebalances()
    assert queued["queued"] >= 1
    assert queued["execute_date"] == exec_date

    monkeypatch.setenv("AFR_PORTFOLIO_RELAX_SESSION", "1")
    monkeypatch.setattr(
        svc,
        "build_from_top_n",
        lambda portfolio_id, **kwargs: {"count": 1, "bought": [], "sold": []},
    )
    import importlib
    import services.trade_pricing as tp

    importlib.reload(tp)

    executed = svc.execute_pending_orders_at_open(as_of=exec_date)
    assert executed["rebalances"] >= 1
    assert executed["errors"] == []


def test_queue_manual_build_top_n(svc):
    pf = svc.create_portfolio("manual", initial_cash=100_000)
    pid = pf["id"]
    r = svc.queue_build_top_n(pid, strategy="composite", top_n=3, min_score=0)
    assert r["queued"] == 1
    assert r["exec_timing"] == "next_open"
    assert r["execute_date"] == svc._exec_date_after_signal()
    pf2 = svc.get_portfolio(pid)
    assert pf2["cash"] == 100_000
    assert len(pf2.get("positions", [])) == 0
