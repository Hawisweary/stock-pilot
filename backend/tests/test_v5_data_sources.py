"""V5 扩展数据源单元/集成测试。"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def v5_db(tmp_path, monkeypatch):
    db_path = tmp_path / "v5_test.db"
    monkeypatch.setenv("TESTING", "1")
    import config

    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(config, "latest_trading_date", lambda db_path=None: "2026-06-04")

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE stocks (
            id INTEGER PRIMARY KEY, code TEXT, name TEXT, is_active INTEGER DEFAULT 1,
            industry_sw TEXT, industry_sw2 TEXT
        );
        CREATE TABLE financial_reports (
            stock_id INTEGER, period_end_date TEXT, report_type TEXT,
            revenue REAL, net_profit REAL, operating_cf REAL, total_assets REAL,
            accounts_receivable REAL
        );
        CREATE TABLE financial_indicators (
            stock_id INTEGER, calc_date TEXT, debt_to_equity REAL
        );
        CREATE TABLE stock_v5_metrics (
            stock_id INTEGER, calc_date TEXT,
            revenue_yoy_q REAL, profit_yoy_q REAL, growth_qoq_delta REAL,
            cfo_np REAL, accrual_ratio REAL, cfo_yoy REAL,
            debt_ratio REAL, debt_vs_industry REAL,
            quality_tier INTEGER, growth_tier INTEGER, source TEXT,
            UNIQUE(stock_id, calc_date)
        );
        CREATE TABLE stock_fund_flow_daily (
            stock_id INTEGER, trade_date TEXT,
            main_net_inflow REAL, super_large_inflow REAL, main_net_5d REAL, source TEXT,
            UNIQUE(stock_id, trade_date)
        );
        CREATE TABLE sector_fund_flow_daily (
            sector_code TEXT, sector_name TEXT, trade_date TEXT,
            net_inflow REAL, net_inflow_pct REAL, change_pct REAL,
            rs_csi300_20d REAL, source TEXT,
            UNIQUE(sector_code, trade_date)
        );
        CREATE TABLE macro_indicators (
            date TEXT PRIMARY KEY, gdp REAL, social_financing REAL,
            social_financing_yoy REAL, bond_yield_10y REAL, usd_cnh REAL
        );
        INSERT INTO stocks (id, code, name, industry_sw) VALUES (1, '300450', '先导智能', '电力设备');
        """
    )
    # 8 个季度样本（简化累计值）
    quarters = [
        ("2025-12-31", 1000, 100, 80, 5000),
        ("2025-09-30", 700, 70, 55, 4800),
        ("2025-06-30", 450, 45, 35, 4600),
        ("2025-03-31", 200, 20, 15, 4400),
        ("2024-12-31", 900, 90, 70, 4200),
        ("2024-09-30", 650, 65, 50, 4000),
    ]
    for pe, rev, np, cfo, assets in quarters:
        conn.execute(
            """INSERT INTO financial_reports
            (stock_id, period_end_date, report_type, revenue, net_profit, operating_cf, total_assets)
            VALUES (1, ?, 'quarterly', ?, ?, ?, ?)""",
            (pe, rev, np, cfo, assets),
        )
    conn.execute(
        "INSERT INTO financial_indicators VALUES (1, '2026-06-04', 0.45)"
    )
    conn.commit()
    conn.close()
    return db_path


def test_compute_v5_metrics(v5_db):
    from services.quality_metrics_calc import compute_stock_v5_metrics

    m = compute_stock_v5_metrics(1)
    assert m is not None
    assert m["cfo_np"] is not None
    assert m["accrual_ratio"] is not None
    assert m["quality_tier"] is not None


def test_compute_all_v5_metrics(v5_db):
    from services.quality_metrics_calc import compute_all_v5_metrics, get_stock_v5_metrics

    r = compute_all_v5_metrics([1])
    assert r["computed"] == 1
    saved = get_stock_v5_metrics(1)
    assert saved is not None
    assert saved["stock_id"] == 1


def test_ignores_dec31_quarterly_when_annual_exists(v5_db):
    """12-31 quarterly 与 annual 并存时不应污染单季同比。"""
    import sqlite3

    from services.quality_metrics_calc import compute_stock_v5_metrics

    conn = sqlite3.connect(v5_db)
    conn.execute("DELETE FROM financial_reports WHERE stock_id=1")
    rows = [
        ("2026-03-31", "q1", 131.0, 12.8, 14.5, 5000),
        ("2025-12-31", "annual", 458.0, 44.5, 53.3, 4900),
        ("2025-12-31", "quarterly", 93.7, 5.8, -3.7, 4800),
        ("2025-09-30", "q3", 112.3, 12.0, 19.7, 4700),
        ("2025-06-30", "q2", 123.9, 13.9, 22.9, 4600),
        ("2025-03-31", "q1", 128.4, 12.7, 14.3, 4500),
        ("2024-03-31", "q1", 106.4, 9.0, 13.9, 4400),
    ]
    for pe, rt, rev, np, cfo, assets in rows:
        conn.execute(
            """INSERT INTO financial_reports
            (stock_id, period_end_date, report_type, revenue, net_profit, operating_cf, total_assets)
            VALUES (1, ?, ?, ?, ?, ?, ?)""",
            (pe, rt, rev, np, cfo, assets),
        )
    conn.commit()
    conn.close()

    m = compute_stock_v5_metrics(1)
    assert m is not None
    assert m["revenue_yoy_q"] is not None
    assert -20 < m["revenue_yoy_q"] <= 25
    assert m["profit_yoy_q"] is not None
    assert abs(m["profit_yoy_q"]) < 50
    assert m["growth_tier"] >= -1
    assert m["cfo_np"] > 0.7


def test_quality_metrics_fallback_when_latest_q1_cfo_missing(v5_db):
    """最新 Q1 缺经营现金流时，应回退到年报计算质量因子。"""
    import sqlite3

    from services.quality_metrics_calc import compute_stock_v5_metrics

    conn = sqlite3.connect(v5_db)
    conn.execute("DELETE FROM financial_reports WHERE stock_id=1")
    rows = [
        ("2026-03-31", "q1", 120.0, 10.0, None, 5000.0),
        ("2025-12-31", "annual", 450.0, 40.0, 55.0, 4900.0),
        ("2024-12-31", "annual", 400.0, 35.0, 50.0, 4700.0),
    ]
    for pe, rt, rev, np, cfo, assets in rows:
        conn.execute(
            """INSERT INTO financial_reports
            (stock_id, period_end_date, report_type, revenue, net_profit, operating_cf, total_assets)
            VALUES (1, ?, ?, ?, ?, ?, ?)""",
            (pe, rt, rev, np, cfo, assets),
        )
    conn.commit()
    conn.close()

    m = compute_stock_v5_metrics(1)
    assert m is not None
    assert m["quality_tier"] is not None
    assert m["cfo_np"] is not None
    assert m["cfo_np"] > 1.0


@pytest.mark.network
def test_fetch_latest_macro_extended():
    from services.eastmoney_macro import fetch_latest_macro

    data = fetch_latest_macro()
    assert data.get("source") == "eastmoney"
    assert data.get("cpi_yoy") is not None or data.get("pmi_manufacturing") is not None
    # 扩展字段至少有一个
    assert any(
        data.get(k) is not None
        for k in ("social_financing", "usd_cnh", "bond_yield_10y")
    )


@pytest.mark.network
def test_sync_sector_fund_flow():
    import requests
    from services.sector_fund_flow_sync import sync_sector_fund_flow

    try:
        r = sync_sector_fund_flow("2026-06-04")
    except requests.RequestException as e:
        pytest.skip(f"东财板块接口暂不可用: {e}")
    assert r["sectors"] > 10
