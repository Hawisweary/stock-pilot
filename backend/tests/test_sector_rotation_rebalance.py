"""行业轮动 — 超目标权重减至目标、欠配补买。"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr("services.trade_pricing._fetch_realtime", lambda code: None)
    monkeypatch.setattr("services.data_sources.tencent_quote", lambda codes: {})
    db_file = str(tmp_path / "test.db")
    import config

    monkeypatch.setattr(config, "DB_PATH", db_file)

    import importlib
    import db_util
    import services.portfolio_svc as svc_mod

    importlib.reload(db_util)
    importlib.reload(svc_mod)

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    svc_mod._ensure_tables(conn)
    today = date.today().isoformat()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            name TEXT,
            is_active INTEGER DEFAULT 1,
            industry_sw TEXT
        );
        CREATE TABLE IF NOT EXISTS stock_daily_quotes (
            stock_id INTEGER NOT NULL,
            trade_date TEXT NOT NULL,
            open REAL, close REAL, high REAL, low REAL,
            volume REAL, UNIQUE(stock_id, trade_date)
        );
        CREATE TABLE IF NOT EXISTS trade_calendar (
            cal_date TEXT PRIMARY KEY, is_open INTEGER NOT NULL
        );
    """)
    conn.execute("INSERT OR REPLACE INTO trade_calendar (cal_date, is_open) VALUES (?,1)", (today,))
    for code, name in [
        ("000101", "持仓A"), ("000102", "持仓B"), ("000103", "新股C"), ("000104", "新股D"),
    ]:
        conn.execute(
            "INSERT INTO stocks (code, name, is_active, industry_sw) VALUES (?,?,1,'测试')",
            (code, name),
        )
        sid = conn.execute("SELECT id FROM stocks WHERE code=?", (code,)).fetchone()[0]
        conn.execute(
            "INSERT INTO stock_daily_quotes (stock_id, trade_date, open, close, high, low, volume) "
            "VALUES (?,?,10,10,10,10,1000000)",
            (sid, today),
        )
    conn.commit()
    conn.close()

    from services.trade_calendar import invalidate_cache
    invalidate_cache()
    yield svc_mod


def test_sector_rotation_trims_to_target_weight(svc, monkeypatch):
    import config

    pf = svc.create_portfolio("sector", initial_cash=10_000)
    pid = pf["id"]
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("UPDATE portfolios SET max_weight_pct=100 WHERE id=?", (pid,))
    conn.commit()
    conn.close()

    assert "error" not in svc.trade(pid, "000101", "buy", 800)
    assert "error" not in svc.trade(pid, "000102", "buy", 100)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("UPDATE portfolio_lots SET buy_date=?", (yesterday,))
    conn.commit()
    conn.close()

    targets = [
        {"code": "000101", "score": 25.0, "name": "持仓A"},
        {"code": "000102", "score": 25.0, "name": "持仓B"},
        {"code": "000103", "score": 25.0, "name": "新股C"},
        {"code": "000104", "score": 25.0, "name": "新股D"},
    ]
    monkeypatch.setattr(
        "services.portfolio_svc.select_sector_rebalance",
        lambda conn, **kwargs: (targets, [], [], None),
    )

    r = svc.build_from_top_n(
        pid, top_n=4, strategy="sector_rotation", pos_style="equal", min_score=0,
    )
    assert "error" not in r, r
    assert r.get("rebalance_mode") == "target_weight"
    assert any(t.get("reason") == "trim_to_target" for t in r.get("trimmed", []))
    assert len(r.get("bought", [])) >= 2
    codes = {p["code"] for p in svc.get_portfolio(pid)["positions"]}
    assert "000103" in codes
    assert "000104" in codes
