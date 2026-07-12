"""V5 资金面多源五档测试。"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def cap_db(tmp_path, monkeypatch):
    db_path = tmp_path / "cap.db"
    import config

    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE stocks (id INTEGER PRIMARY KEY, code TEXT, is_active INTEGER DEFAULT 1);
        CREATE TABLE stock_fund_flow_daily (
            stock_id INTEGER, trade_date TEXT, main_net_5d REAL
        );
        CREATE TABLE eastmoney_margin (
            stock_id INTEGER, date TEXT, margin_balance REAL, margin_buy REAL
        );
        CREATE TABLE lhb_daily (
            stock_id INTEGER, trade_date TEXT, code TEXT, net_buy REAL,
            buy_amount REAL, sell_amount REAL, deal_amount REAL,
            change_pct REAL, turnover_pct REAL, reason TEXT, source TEXT,
            PRIMARY KEY (stock_id, trade_date)
        );
        CREATE TABLE eastmoney_holdings (
            code TEXT, date TEXT, shares REAL, ratio REAL, UNIQUE(code, date)
        );
        INSERT INTO stocks VALUES (1, '300450', 1);
        INSERT INTO stock_fund_flow_daily VALUES (1, '2026-06-04', 80000000);
        INSERT INTO eastmoney_margin VALUES
            (1, '2026-06-04', 1100, 0),
            (1, '2026-06-03', 1080, 0),
            (1, '2026-05-30', 1050, 0),
            (1, '2026-05-29', 1040, 0),
            (1, '2026-05-28', 1030, 0),
            (1, '2026-05-27', 1000, 0);
        INSERT INTO lhb_daily VALUES
            (1, '2026-06-04', '300450', 3000, 0, 0, 0, 0, 0, '', 'test'),
            (1, '2026-06-01', '300450', 2000, 0, 0, 0, 0, 0, '', 'test');
        INSERT INTO eastmoney_holdings VALUES
            ('300450', '2026-06-04', 1000, 0.05),
            ('300450', '2026-05-01', 900, 0.03);
        """
    )
    conn.commit()
    conn.close()
    return db_path


def test_capital_multisource_tier(cap_db):
    import config
    from services.capital_tier_v5 import compute_capital_tier_v5

    conn = sqlite3.connect(str(cap_db))
    r = compute_capital_tier_v5(1, conn, code="300450")
    conn.close()
    assert r["tier"] is not None
    assert r["tier"] >= 1
    assert r["sub_tiers"]["main_flow"] == 2
    assert "main_flow" in r["effective_weights"]
    assert abs(sum(r["effective_weights"].values()) - 1.0) < 0.01
