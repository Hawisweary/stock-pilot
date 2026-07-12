"""V5 Phase 3 W2 — 政策事件/T+20 响应 + 情绪代理翻转。"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def w2_db(tmp_path, monkeypatch):
    db_path = tmp_path / "w2.db"
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
        CREATE TABLE stock_announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER, title TEXT, ann_type TEXT, pub_date TEXT,
            source TEXT DEFAULT 'eastmoney', art_code TEXT, event_type TEXT DEFAULT ''
        );
        CREATE TABLE stock_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER, title TEXT, pub_date TEXT,
            sentiment_score REAL, event_type TEXT DEFAULT ''
        );
        CREATE TABLE stock_daily_quotes (
            stock_id INTEGER, trade_date TEXT, close REAL, volume REAL,
            change_pct REAL, turnover REAL
        );
        CREATE TABLE stock_fund_flow_daily (
            stock_id INTEGER, trade_date TEXT,
            main_net_inflow REAL, main_net_5d REAL, source TEXT
        );
        CREATE TABLE policy_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pub_date TEXT, title TEXT, level INTEGER,
            industries_json TEXT, source TEXT,
            UNIQUE(pub_date, title)
        );
        CREATE TABLE policy_industry_response (
            event_id INTEGER, industry_sw2 TEXT,
            excess_return_20d REAL, coef REAL,
            UNIQUE(event_id, industry_sw2)
        );
        CREATE TABLE stock_mood_v5_daily (
            stock_id INTEGER, calc_date TEXT, mood_raw REAL, mood_tier INTEGER,
            turnover_pct REAL, news_heat REAL, main_net_5d REAL,
            capital_tier INTEGER, flipped INTEGER, source TEXT,
            UNIQUE(stock_id, calc_date)
        );
        INSERT INTO stocks VALUES
            (1, '300450', '先导智能', 1, '电力设备', '电池'),
            (2, '600519', '贵州茅台', 1, '食品饮料', '白酒');
        INSERT INTO stock_announcements (stock_id, title, pub_date, art_code) VALUES
            (1, '国务院发布新能源汽车以旧换新补贴政策', '2026-03-01', 'p1');
        INSERT INTO stock_news (stock_id, title, pub_date, sentiment_score) VALUES
            (1, '公司获重大政策支持', '2026-05-28', 98),
            (1, '市场情绪极度乐观', '2026-05-29', 99),
            (1, '分析师集体上调评级', '2026-05-30', 97);
        """
    )
    # 60 日行情：高换手
    for i in range(60):
        conn.execute(
            """INSERT INTO stock_daily_quotes
            (stock_id, trade_date, close, turnover)
            VALUES (1, date('2026-06-04', ?), 10.0, ?)""",
            (f"-{i} days", 0.2 if i > 0 else 12.0),
        )
    conn.execute(
        """INSERT INTO stock_fund_flow_daily VALUES
        (1, '2026-06-04', -1000000, -800000000, 'eastmoney')"""
    )
    conn.commit()
    conn.close()
    return db_path


def test_policy_title_detection():
    from services.policy_event_sync import (
        _industries_from_title,
        _is_policy_title,
        _policy_level_from_score,
    )

    assert _is_policy_title("国务院发布新能源汽车以旧换新补贴政策")
    assert "汽车" in _industries_from_title("国务院发布新能源汽车以旧换新补贴政策")
    assert _policy_level_from_score(25) == 2
    assert _policy_level_from_score(-25) == -2


def test_v5_flip_rule_unit():
    from services.mood_scorer import apply_v5_flip

    assert apply_v5_flip(2, -1) == (0, True)
    assert apply_v5_flip(2, 1) == (2, False)
    assert apply_v5_flip(1, -1) == (1, False)
    assert apply_v5_flip(-2, 1) == (1, True)


def test_sync_policy_events(w2_db, monkeypatch):
    import config
    from services.policy_event_sync import get_policy_events, sync_policy_events

    monkeypatch.setattr(config, "DB_PATH", str(w2_db))
    r = sync_policy_events(lookback_days=120)
    assert r["added"] >= 1
    items = get_policy_events()
    assert any(it["level"] != 0 for it in items)
    assert any(
        "汽车" in it.get("industries", []) or "宏观" in it.get("industries", [])
        for it in items
    )


def test_mood_flip_rule(w2_db, monkeypatch):
    import config
    from services.mood_scorer import compute_all_mood_v5, compute_mood_proxy

    monkeypatch.setattr(config, "DB_PATH", str(w2_db))
    m = compute_mood_proxy(1, market_limit_up=90)
    assert m["turnover_pct"] >= 90
    assert m["capital_tier"] <= 0
    assert m["mood_raw"] >= 60

    from services.mood_scorer import apply_v5_flip

    tier, flipped = apply_v5_flip(2, m["capital_tier"])
    assert flipped is True
    assert tier == 0

    r = compute_all_mood_v5([1])
    assert r["computed"] == 1


def test_policy_score_v5(w2_db, monkeypatch):
    import config
    from services.policy_event_sync import get_policy_score_v5_for_stock

    monkeypatch.setattr(config, "DB_PATH", str(w2_db))
    conn = sqlite3.connect(w2_db)
    conn.execute(
        """INSERT INTO policy_events (pub_date, title, level, industries_json, source)
           VALUES ('2026-05-01', '新型电力系统投资加码', 2, ?, 'test')""",
        (json.dumps(["电力设备"]),),
    )
    conn.execute(
        """INSERT INTO policy_industry_response
           (event_id, industry_sw2, excess_return_20d, coef)
           VALUES (1, '电力设备', 5.0, 1.2)"""
    )
    conn.execute(
        """INSERT INTO policy_events (pub_date, title, level, industries_json, source)
           VALUES ('2026-05-02', '半导体板块走强', 2, ?, 'test')""",
        (json.dumps(["电子"]),),
    )
    conn.execute(
        """INSERT INTO policy_industry_response
           (event_id, industry_sw2, excess_return_20d, coef)
           VALUES (2, '电子', 4.0, 1.5)"""
    )
    conn.commit()
    conn.close()

    s = get_policy_score_v5_for_stock(1)
    assert s is not None
    assert s["tier"] >= 1
    assert len(s["events"]) == 1
    assert s["events"][0]["industry"] == "电力设备"
