"""API 集成测试 — 临时库 + mock 抓取，不访问外网"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(__file__))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)

os.environ["TESTING"] = "1"
os.environ["AFR_API_KEY_REQUIRED"] = "false"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "integration.db"
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)

    monkeypatch.setenv("AFR_API_KEY_REQUIRED", "false")
    import config

    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(config, "DATA_DIR", str(data_dir))

    import database as db

    db._conn = None
    db.init(str(db_path))

    import services.fetch_job as fj

    monkeypatch.setattr(fj, "DB_PATH", str(db_path))

    def _fake_fetch(stock_id, code, market):
        return {
            "quotes_count": 5,
            "financials_count": 4,
            "indicators_count": 3,
            "status": "success",
            "errors": [],
        }

    monkeypatch.setattr(fj, "sync_fetch_one", _fake_fetch)

    from app import app
    from fastapi.testclient import TestClient

    c = TestClient(app)
    db.get().execute(
        "INSERT INTO stocks (code, name, market, is_active) VALUES ('600519', '茅台', 'A', 1)"
    )
    db.get().commit()
    return c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_single_fetch_status_flow(client):
    r = client.post("/api/data/fetch/1")
    assert r.status_code == 200
    assert r.json()["status"] in ("started", "already_running")

    for _ in range(20):
        s = client.get("/api/data/fetch/1/status").json()
        if not s.get("running"):
            break
    assert s["status"] == "success"
    assert s["quotes"] == 5


def test_factor_weights_roundtrip(client):
    r = client.put(
        "/api/scores/factor-weights",
        json={
            "quality": 0.28,
            "growth": 0.22,
            "value": 0.2,
            "momentum": 0.15,
            "risk": 0.15,
        },
    )
    assert r.status_code == 200
    w = client.get("/api/scores/factor-weights").json()
    assert abs(w["quality"] - 0.28) < 0.001


def test_fetch_logs_summary(client):
    client.post("/api/data/fetch/1")
    summary = client.get("/api/data/fetch-logs-summary").json()
    assert "summary" in summary


def test_report_export(client):
    r = client.get("/api/stocks/1/report/export")
    assert r.status_code == 200
    assert "attachment" in r.headers.get("content-disposition", "").lower()
    assert "600519" in r.text
