"""V5 Phase 3 — EPS 修正、事件分类、风险标记。"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def phase3_db(tmp_path, monkeypatch):
    db_path = tmp_path / "phase3.db"
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
            url TEXT, pdf_url TEXT, source TEXT, art_code TEXT, event_type TEXT DEFAULT ''
        );
        CREATE TABLE stock_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER, title TEXT, pub_date TEXT, event_type TEXT DEFAULT ''
        );
        CREATE TABLE stock_eps_forecast (
            stock_id INTEGER, as_of_date TEXT, eps_fy1 REAL, eps_fy2 REAL,
            eps_fy1_year INTEGER, eps_fy2_year INTEGER, analyst_count INTEGER,
            rating_buy INTEGER, industry_board TEXT, revision_3m_pct REAL, source TEXT,
            UNIQUE(stock_id, as_of_date)
        );
        CREATE TABLE industry_eps_revision_daily (
            industry_sw2 TEXT, trade_date TEXT, revision_3m_pct REAL,
            stock_count INTEGER, tier INTEGER, source TEXT,
            UNIQUE(industry_sw2, trade_date)
        );
        CREATE TABLE risk_flags (
            stock_id INTEGER, flag_date TEXT, flag_type TEXT,
            severity TEXT, detail TEXT, source TEXT,
            UNIQUE(stock_id, flag_date, flag_type)
        );
        CREATE TABLE stock_daily_quotes (
            stock_id INTEGER, trade_date TEXT, close REAL, volume REAL,
            change_pct REAL, turnover REAL
        );
        INSERT INTO stocks VALUES
            (1, '300450', '先导智能', 1, '电力设备', '电池'),
            (2, '000004', '*ST国华', 1, '计算机', '软件');
        INSERT INTO stock_announcements (stock_id, title, pub_date, art_code) VALUES
            (1, '关于签订重大合同的公告', '2026-05-01', 'a1'),
            (1, '2025年年度报告', '2026-04-20', 'a2'),
            (2, '关于收到证监会立案调查通知书的公告', '2026-05-10', 'b1');
        INSERT INTO stock_news (stock_id, title, pub_date) VALUES
            (1, '公司股东拟减持股份计划公告', '2026-05-02');
        INSERT INTO stock_eps_forecast VALUES
            (1, '2026-03-01', 0.8, 1.2, 2025, 2026, 10, 8, '电池', NULL, 'eastmoney'),
            (1, '2026-06-04', 0.9, 1.5, 2025, 2026, 12, 9, '电池', NULL, 'eastmoney');
        INSERT INTO stock_daily_quotes VALUES
            (2, '2026-06-04', 5.0, 100, -9.9, 0.1),
            (2, '2026-06-03', 5.5, 120, -9.8, 0.2),
            (2, '2026-06-02', 6.1, 80, -9.7, 0.1);
        """
    )
    conn.commit()
    conn.close()
    return db_path


def test_classify_event_title():
    from services.event_classifier import classify_event_title

    assert classify_event_title("签订重大合同公告") == "contract"
    assert classify_event_title("2025年三季度报告") == "fundamental"
    assert classify_event_title("股东减持计划") == "sell_down"
    assert classify_event_title("证监会立案调查") == "investigation"
    assert classify_event_title("关于立案调查的公告") == "investigation"
    assert classify_event_title("关于以集中竞价交易方式回购A股股份进展情况的公告") == "buyback"
    assert classify_event_title("关于公司高级管理人员离任的公告") == "management_change"
    assert classify_event_title("关于实施员工持股计划方案的公告") == "equity_incentive"
    assert classify_event_title("关于收到政府补助的公告") == "subsidy"
    assert classify_event_title("控股股东增持计划公告") == "increase_holdings"
    assert classify_event_title("近日海外机构调研股名单") == "institutional_research"
    assert classify_event_title("37.44亿元主力资金今日撤离计算机板块") == ""
    assert classify_event_title("关于签订2亿元产品采购合同的公告") == "contract"


def test_classify_announcements(phase3_db, monkeypatch):
    import config
    from services.event_classifier import classify_announcements, get_stock_events

    monkeypatch.setattr(config, "DB_PATH", str(phase3_db))
    r = classify_announcements(reclassify=True)
    assert r["scanned"] >= 3
    assert r["classified"] >= 2

    conn = sqlite3.connect(phase3_db)
    rows = conn.execute(
        "SELECT title, event_type FROM stock_announcements ORDER BY id"
    ).fetchall()
    conn.close()
    by_title = {t: et for t, et in rows}
    assert by_title["关于签订重大合同的公告"] == "contract"
    assert by_title["2025年年度报告"] == "fundamental"
    assert by_title["关于收到证监会立案调查通知书的公告"] == "investigation"

    events = get_stock_events(1, include_fundamental=False)
    types = {e["event_type"] for e in events}
    assert "contract" in types


def test_industry_eps_revision(phase3_db, monkeypatch):
    import config

    monkeypatch.setattr(config, "DB_PATH", str(phase3_db))
    from services.eastmoney_forecast_sync import sync_industry_eps_revision

    # 先补 revision_3m_pct
    conn = sqlite3.connect(phase3_db)
    conn.execute(
        "UPDATE stock_eps_forecast SET revision_3m_pct=25.0 WHERE stock_id=1 AND as_of_date='2026-06-04'"
    )
    conn.commit()
    conn.close()

    r = sync_industry_eps_revision(trade_date="2026-06-04")
    assert r["industries"] == 1

    conn = sqlite3.connect(phase3_db)
    row = conn.execute(
        "SELECT tier, revision_3m_pct FROM industry_eps_revision_daily WHERE industry_sw2='电池'"
    ).fetchone()
    conn.close()
    assert row[0] == 2
    assert row[1] == 25.0


def test_risk_scanner(phase3_db, monkeypatch):
    import config

    monkeypatch.setattr(config, "DB_PATH", str(phase3_db))
    from services.event_classifier import classify_announcements
    from services.risk_scanner import get_risk_flags, has_veto_risk, scan_risk_flags

    classify_announcements(reclassify=True)
    r = scan_risk_flags()
    assert r["st"] >= 1
    flags = get_risk_flags(2)
    assert any(f["flag_type"] == "st" for f in flags)
    assert has_veto_risk(2)


def test_classify_events_llm(phase3_db, monkeypatch):
    import config

    monkeypatch.setattr(config, "DB_PATH", str(phase3_db))
    conn = sqlite3.connect(phase3_db)
    conn.execute("DELETE FROM stock_announcements WHERE stock_id=1")
    conn.execute(
        """INSERT INTO stock_announcements (stock_id, title, pub_date, art_code, event_type)
           VALUES (1, '第五届董事会第十六次会议决议公告', '2026-05-28', 'x1', '')"""
    )
    conn.commit()
    conn.close()

    def _fake_llm(titles):
        return [
            "management_change" if "董事会" in (t or "") else ""
            for t in titles
        ]

    monkeypatch.setattr(
        "services.event_classifier_llm.classify_titles_llm", _fake_llm
    )
    monkeypatch.setattr("services.event_classifier_llm.is_llm_available", lambda: True)

    from services.event_classifier_llm import classify_events_llm

    r = classify_events_llm([1], limit_per_stock=5)
    assert r["classified_total"] >= 1

    conn = sqlite3.connect(phase3_db)
    et = conn.execute(
        "SELECT event_type FROM stock_announcements WHERE art_code='x1'"
    ).fetchone()[0]
    conn.close()
    assert et == "management_change"


def test_parse_llm_batch():
    from services.event_classifier_llm import _parse_llm_batch

    raw = '{"items":[{"idx":1,"event_type":"buyback"},{"idx":2,"event_type":"invalid"}]}'
    assert _parse_llm_batch(raw, 2) == ["buyback", ""]


def test_event_title_cache_roundtrip(phase3_db, monkeypatch):
    import config

    monkeypatch.setattr(config, "DB_PATH", str(phase3_db))
    from services.event_title_cache import lookup_titles, store_titles

    store_titles({"回购进展公告": "buyback", "板块大涨": ""})
    hit = lookup_titles(["回购进展公告", "板块大涨", "未知标题"])
    assert hit["回购进展公告"] == "buyback"
    assert hit["板块大涨"] == ""
    assert "未知标题" not in hit


def test_llm_only_missing_news_filter(phase3_db, monkeypatch):
    import config

    monkeypatch.setattr(config, "DB_PATH", str(phase3_db))
    conn = sqlite3.connect(phase3_db)
    conn.execute(
        """INSERT INTO stock_announcements
           (stock_id, title, pub_date, art_code, event_type)
           VALUES (1, '股份回购进展公告', '2026-05-28', 'a1', 'buyback')"""
    )
    conn.commit()
    conn.close()

    from services.v5_data_sync import stocks_missing_news_events

    missing = stocks_missing_news_events()
    assert 1 not in missing
    assert 2 in missing


def test_sync_mode_daily_preset():
    from services.v5_data_sync import V5_SYNC_MODE_PRESETS

    daily = V5_SYNC_MODE_PRESETS["daily"]
    assert daily["skip_announcements"] is True
    assert daily["llm_only_missing_news"] is True
    nightly = V5_SYNC_MODE_PRESETS["nightly"]
    assert nightly["skip_v5_scores"] is True
    assert nightly["skip_announcements"] is False


@pytest.mark.network
def test_fetch_eps_forecast_for_code():
    from services.eastmoney_forecast_sync import fetch_eps_forecast_for_code

    row = fetch_eps_forecast_for_code("300450")
    assert row is not None
    assert row.get("SECURITY_CODE") == "300450"
    assert row.get("EPS2") is not None
