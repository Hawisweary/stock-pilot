"""V5 十维打分算法测试。"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def scorer_db(tmp_path, monkeypatch):
    db_path = tmp_path / "scorer.db"
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
        CREATE TABLE stock_v5_metrics (
            stock_id INTEGER, calc_date TEXT, growth_tier INTEGER, quality_tier INTEGER,
            growth_qoq_delta REAL, UNIQUE(stock_id, calc_date)
        );
        CREATE TABLE stock_fund_flow_daily (
            stock_id INTEGER, trade_date TEXT, main_net_5d REAL
        );
        CREATE TABLE tech_analysis_cache (
            stock_id INTEGER, input_hash TEXT, daily_close REAL, weekly_close REAL,
            score REAL, signal TEXT, advice TEXT, reasoning TEXT, full_result TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE comprehensive_scores (
            stock_id INTEGER, calc_date TEXT, val_score REAL, technical_score REAL,
            quality_score REAL, industry_score REAL, capital_score REAL, policy_score REAL,
            market_env_score REAL, composite_v5 REAL, veto_status TEXT, v5_breakdown_json TEXT,
            UNIQUE(stock_id, calc_date)
        );
        CREATE TABLE stock_mood_v5_daily (
            stock_id INTEGER, calc_date TEXT, mood_tier INTEGER
        );
        CREATE TABLE industry_eps_revision_daily (
            industry_sw2 TEXT, trade_date TEXT, tier INTEGER
        );
        CREATE TABLE sector_fund_flow_daily (
            sector_name TEXT, trade_date TEXT, rs_csi300_20d REAL,
            net_inflow_pct REAL, change_pct REAL
        );
        CREATE TABLE macro_indicators (
            date TEXT, pmi_manufacturing REAL, social_financing_yoy REAL
        );
        CREATE TABLE stock_announcements (
            id INTEGER PRIMARY KEY, stock_id INTEGER, title TEXT, pub_date TEXT,
            event_type TEXT, ann_type TEXT, source TEXT
        );
        CREATE TABLE policy_events (
            id INTEGER PRIMARY KEY, pub_date TEXT, title TEXT, level INTEGER,
            industries_json TEXT, source TEXT
        );
        CREATE TABLE policy_industry_response (
            event_id INTEGER, industry_sw2 TEXT, excess_return_20d REAL, coef REAL
        );
        CREATE TABLE risk_flags (
            stock_id INTEGER, flag_date TEXT, flag_type TEXT,
            severity TEXT, detail TEXT, source TEXT,
            UNIQUE(stock_id, flag_date, flag_type)
        );
        CREATE TABLE stock_daily_quotes (
            stock_id INTEGER, trade_date TEXT, close REAL
        );
        CREATE TABLE data_quality_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, stock_id INTEGER, trade_date TEXT,
            anomaly_score REAL, flags TEXT, severity TEXT, isolation_score REAL,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(stock_id, trade_date)
        );
        CREATE TABLE market_regime_daily (
            trade_date TEXT PRIMARY KEY, index_code TEXT, regime TEXT,
            rsi_14 REAL, volatility_20 REAL, adx REAL,
            return_20d REAL, return_60d REAL, price_vs_ma20 REAL, price_vs_ma60 REAL,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        INSERT INTO stock_daily_quotes VALUES (1, '2026-06-04', 10.0);
        INSERT INTO stocks VALUES (1, '300450', '先导智能', 1, '电力设备', '电池');
        INSERT INTO stock_v5_metrics VALUES (1, '2026-06-04', 2, -2, 5.0);
        INSERT INTO stock_fund_flow_daily VALUES (1, '2026-06-04', -100000000);
        INSERT INTO comprehensive_scores (stock_id, calc_date, val_score, technical_score)
            VALUES (1, '2026-06-04', 55, 70);
        INSERT INTO stock_mood_v5_daily VALUES (1, '2026-06-04', 0);
        INSERT INTO industry_eps_revision_daily VALUES ('电池', '2026-06-04', 1);
        INSERT INTO sector_fund_flow_daily VALUES ('电池', '2026-06-04', 3.5, 1.2, 2.1);
        INSERT INTO macro_indicators VALUES ('2026-06-04', 50.5, 8.0);
        INSERT INTO stock_announcements (stock_id, title, pub_date, event_type)
            VALUES (1, '签订重大合同', '2026-06-01', 'contract');
        """
    )
    conn.commit()
    conn.close()
    return db_path


def test_tier_to_pct():
    from services.v5_scorer import tier_to_pct

    assert tier_to_pct(-2) == 0
    assert tier_to_pct(0) == 50
    assert tier_to_pct(2) == 100


def test_shortboard_penalty():
    from services.v5_scorer import _shortboard_penalty

    assert _shortboard_penalty({"quality": -2, "valuation": -1}) == 13.0


def test_news_intensity_sum():
    from services.v5_scorer import _news_tier_from_events

    events = [
        {"event_type": "contract"},
        {"event_type": "subsidy"},
    ]
    assert _news_tier_from_events(events) == 2
    assert _news_tier_from_events([{"event_type": "investigation"}]) == -2


def test_veto_quality(scorer_db, monkeypatch):
    import config
    from services.v5_scorer import check_veto

    monkeypatch.setattr(config, "DB_PATH", str(scorer_db))
    status, reasons, flags = check_veto(1, {"quality": -2, "market_env": 0})
    assert status == "discount"
    assert flags.get("quality_minus2")
    assert any("质量" in r for r in reasons)


def test_compute_stock_v5(scorer_db, monkeypatch):
    import config
    from services.v5_scorer import compute_all_v5_scores, compute_stock_v5_tiers

    monkeypatch.setattr(config, "DB_PATH", str(scorer_db))
    r = compute_stock_v5_tiers(1, market_env_tier=0)
    assert r["tiers"]["fundamental"] == 2
    assert r["tiers"]["quality"] == -2
    assert r["shortboard_penalty"] >= 10
    assert r["veto_status"] == "discount"
    assert r["dims_available"] is not None
    assert 0 <= r["composite_v5"] <= 100

    batch = compute_all_v5_scores([1], calc_date="2026-06-04")
    assert batch["computed"] == 1
    assert batch["veto_exclude"] == 0

    conn = sqlite3.connect(scorer_db)
    row = conn.execute(
        "SELECT composite_v5, veto_status, v5_breakdown_json FROM comprehensive_scores WHERE stock_id=1"
    ).fetchone()
    conn.close()
    assert row[0] is not None
    assert row[1] == "discount"
    breakdown = json.loads(row[2])
    assert breakdown["tiers"]["quality"] == -2


def test_veto_data_quality_exclude(scorer_db, monkeypatch):
    import config
    from services.v5_scorer import check_veto

    monkeypatch.setattr(config, "DB_PATH", str(scorer_db))
    conn = sqlite3.connect(scorer_db)
    conn.execute(
        "INSERT INTO data_quality_alerts (stock_id, trade_date, anomaly_score, flags, severity) VALUES (1, '2026-06-04', 85, '[\"price_spike\"]', 'critical')"
    )
    conn.commit()
    conn.close()

    status, reasons, flags = check_veto(1, {"quality": 0, "market_env": 0}, calc_date="2026-06-04")
    assert status == "exclude"
    assert flags.get("data_quality") is False
    assert any("数据质量" in r for r in reasons)


def test_veto_data_quality_discount(scorer_db, monkeypatch):
    import config
    from services.v5_scorer import check_veto

    monkeypatch.setattr(config, "DB_PATH", str(scorer_db))
    conn = sqlite3.connect(scorer_db)
    conn.execute(
        "INSERT INTO data_quality_alerts (stock_id, trade_date, anomaly_score, flags, severity) VALUES (1, '2026-06-04', 60, '[\"volume_burst\"]', 'warning')"
    )
    conn.commit()
    conn.close()

    status, reasons, flags = check_veto(1, {"quality": 0, "market_env": 0}, calc_date="2026-06-04")
    assert status == "discount"
    assert flags.get("data_quality")
    assert any("数据质量" in r for r in reasons)


def test_compute_stock_v5_data_quality_discount(scorer_db, monkeypatch):
    import config
    from services.v5_scorer import compute_stock_v5_tiers

    monkeypatch.setattr(config, "DB_PATH", str(scorer_db))
    conn = sqlite3.connect(scorer_db)
    conn.execute(
        "INSERT INTO data_quality_alerts (stock_id, trade_date, anomaly_score, flags, severity) VALUES (1, '2026-06-04', 60, '[\"volume_burst\"]', 'warning')"
    )
    conn.commit()
    conn.close()

    r = compute_stock_v5_tiers(1, market_env_tier=0, calc_date="2026-06-04")
    assert r["veto_status"] == "discount"
    assert r.get("discount_flags", {}).get("data_quality")
    assert any("数据质量" in reason for reason in r.get("veto_reasons", []))
