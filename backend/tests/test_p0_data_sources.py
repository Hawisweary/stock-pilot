"""P0-P1 数据源：东财三表 / 宏观 / 复权 / 公告。"""
from __future__ import annotations

import sqlite3

import pytest


@pytest.mark.network
def test_eastmoney_profit_sheet():
    from services.eastmoney_finance import fetch_profit_sheet

    df = fetch_profit_sheet("600519", "yearly")
    assert df is not None
    assert not df.empty
    assert "REPORT_DATE" in df.columns


@pytest.mark.network
def test_eastmoney_macro_latest():
    from services.eastmoney_macro import fetch_latest_macro

    data = fetch_latest_macro()
    assert data.get("source") == "eastmoney"
    assert data.get("cpi_yoy") is not None or data.get("pmi_manufacturing") is not None


@pytest.mark.network
def test_ex_rights_and_adj():
    from services.adjust_factor_sync import fetch_ex_rights_events, apply_forward_adj, sync_ex_rights

    events = fetch_ex_rights_events("600519", page_size=10)
    assert isinstance(events, list)

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE stocks (id INTEGER PRIMARY KEY, code TEXT)")
    conn.execute("INSERT INTO stocks VALUES (1, '600519')")
    conn.execute(
        """CREATE TABLE stock_daily_quotes (
            stock_id INTEGER, trade_date TEXT, open REAL, high REAL, low REAL,
            close REAL, volume REAL, adj_close REAL, UNIQUE(stock_id, trade_date))"""
    )
    conn.execute(
        "INSERT INTO stock_daily_quotes VALUES (1,'2024-01-02',100,101,99,100,1000,NULL)"
    )
    conn.execute(
        "INSERT INTO stock_daily_quotes VALUES (1,'2024-06-26',90,91,89,90,1000,NULL)"
    )
    n = sync_ex_rights(1, "600519", conn=conn)
    assert n >= 0
    r = apply_forward_adj(1, quote_source="qfq", conn=conn)
    assert r["mode"] == "qfq_passthrough"
    row = conn.execute(
        "SELECT adj_close FROM stock_daily_quotes WHERE stock_id=1 AND trade_date='2024-01-02'"
    ).fetchone()
    assert row and row[0] == 100.0


@pytest.mark.network
def test_announcements_fetch():
    from services.announcement_fetch import fetch_announcements_for_stock

    rows = fetch_announcements_for_stock("600519", limit=5)
    assert len(rows) >= 1
    assert rows[0].get("title")
    assert rows[0].get("url")
