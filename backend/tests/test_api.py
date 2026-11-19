"""API 测试 — 核心端点冒烟测试（隔离临时库）"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["TESTING"] = "1"
os.environ.setdefault("AFR_API_KEY_REQUIRED", "false")


@pytest.fixture()
def client(tmp_path, monkeypatch, redirect_db):
    db_path = tmp_path / "api_test.db"
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("AFR_API_KEY_REQUIRED", "false")
    import config

    redirect_db(db_path, tmp_path / "cache.db")
    monkeypatch.setattr(config, "DATA_DIR", str(data_dir))

    import database as db

    if db._conn is not None:
        try:
            db._conn.close()
        except Exception:
            pass
        db._conn = None

    db.init(str(db_path))
    # app 导入时可能才首次加载新模块,它们拷贝的是已 patch 的路径;这里再统一一次
    from app import app  # noqa: F401

    redirect_db(db_path, tmp_path / "cache.db")
    conn = db.get()
    # 以下表在生产库由各服务懒建,迁移里没有;空测试库需手动补
    conn.execute(
        """CREATE TABLE IF NOT EXISTS capital_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT, stock_id INTEGER NOT NULL,
            date TEXT NOT NULL, composite_score REAL, flow_score REAL,
            turnover_score REAL, volume_score REAL, change_score REAL,
            holder_score REAL, breakdown_json TEXT,
            UNIQUE(stock_id, date))"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS policy_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT, stock_id INTEGER NOT NULL,
            date TEXT NOT NULL, composite_score REAL, keyword_score REAL,
            llm_score REAL, subsidy_score REAL, breakdown_json TEXT,
            UNIQUE(stock_id, date))"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS sentiment_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT, stock_id INTEGER NOT NULL,
            date TEXT NOT NULL, composite_score REAL, turnover_score REAL,
            leverage_score REAL, limit_score REAL, rsi_score REAL,
            breakdown_json TEXT, UNIQUE(stock_id, date))"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS valuation_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT, stock_id INTEGER NOT NULL,
            date TEXT NOT NULL, composite_score REAL, pe_score REAL, pb_score REAL,
            div_score REAL, peg_score REAL, breakdown_json TEXT,
            UNIQUE(stock_id, date))"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS tech_analysis_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT, stock_id INTEGER NOT NULL,
            input_hash TEXT NOT NULL, daily_close REAL, weekly_close REAL,
            score INTEGER, signal TEXT, advice TEXT, reasoning TEXT,
            full_result TEXT, created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(stock_id, input_hash))"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS stock_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT, stock_id INTEGER NOT NULL,
            title TEXT NOT NULL, content TEXT, source TEXT, pub_date TEXT NOT NULL,
            url TEXT, sentiment_score REAL, sentiment_label TEXT, ai_summary TEXT,
            fetched_at TEXT DEFAULT (datetime('now')), event_type TEXT DEFAULT '',
            UNIQUE(stock_id, title, pub_date))"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS stock_industries (
            stock_id INTEGER, industry_id INTEGER)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS industry_tags (
            id INTEGER PRIMARY KEY, name TEXT)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS debate_v2 (
            stock_id INTEGER, date TEXT, adjusted_score REAL)"""
    )
    conn.execute(
        """INSERT OR IGNORE INTO stocks (code, name, market, is_active)
           VALUES ('600519', '贵州茅台', 'A', 1)"""
    )
    conn.commit()

    from app import app
    from fastapi.testclient import TestClient

    return TestClient(app)


def test_list_stocks_returns_200(client):
    r = client.get("/api/stocks")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) >= 1


def test_comprehensive_scores_endpoint(client):
    sid = client.get("/api/stocks").json()[0]["id"]
    r = client.get(f"/api/stocks/{sid}/comprehensive")
    assert r.status_code == 200
    d = r.json()
    assert "previous" in d


def test_feature_flags(client):
    r = client.get("/api/system/features")
    assert r.status_code == 200
    assert "backtest" in r.json()


def test_scores_ranking(client):
    r = client.get("/api/scores/ranking?limit=5")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_scores_batch(client):
    r = client.get("/api/scores/batch")
    assert r.status_code == 200
    data = r.json()
    assert "comprehensive" in data
    assert "debate_scores" not in data
    assert isinstance(data["comprehensive"], list)


def test_scores_sparkline(client):
    r = client.post("/api/scores/sparkline", json={"stock_ids": [1], "days": 7})
    assert r.status_code == 200
    body = r.json()
    assert body["metric"] == "composite_v5"
    assert "series" in body
    assert "1" in body["series"]


def test_scores_trend_v5(client):
    r = client.get("/api/scores/trend/1?days=7")
    assert r.status_code == 200
    data = r.json()
    assert data.get("metric") == "composite_v5"
    if data.get("trend"):
        assert "score" in data["trend"][0]


def test_calendar_upcoming(client):
    r = client.get("/api/calendar/upcoming?limit=5")
    assert r.status_code == 200
    assert "events" in r.json()
