"""批量抓取 API — 双模式与步骤状态"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["TESTING"] = "1"
os.environ.setdefault("AFR_API_KEY_REQUIRED", "false")


@pytest.fixture(autouse=True)
def _isolate_batch_fetch(monkeypatch):
    """避免 fetch-all 后台线程占用 SQLite 锁。"""
    import api.data as data_mod

    class _NoopThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            self._target = target

        def start(self):
            return None

    monkeypatch.setattr(data_mod.threading, "Thread", _NoopThread)
    data_mod._reset_fetch_all_status()
    yield
    data_mod._reset_fetch_all_status()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "fetch_api.db"
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("AFR_API_KEY_REQUIRED", "false")
    import config

    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(config, "DATA_DIR", str(data_dir))

    import database as db

    if db._conn is not None:
        try:
            db._conn.close()
        except Exception:
            pass
        db._conn = None

    db.init(str(db_path))
    conn = db.get()
    conn.execute(
        """INSERT OR IGNORE INTO stocks (code, name, market, is_active)
           VALUES ('600519', '贵州茅台', 'A', 1)"""
    )
    conn.commit()

    from app import app
    from fastapi.testclient import TestClient

    return TestClient(app)


def _stock_id() -> int:
    import database as db

    row = db.get().execute("SELECT id FROM stocks LIMIT 1").fetchone()
    assert row is not None
    return int(row[0])


def test_fetch_step_status_empty(client):
    r = client.get("/api/data/fetch-step-status")
    assert r.status_code == 200
    assert r.json()["summary"] == {}


def test_fetch_step_status_roundtrip(client):
    from services.fetch_step_status import record_step

    sid = _stock_id()
    record_step(sid, "financials", "skipped", "skipped_by_plan")
    record_step(sid, "quotes", "error", "timeout")

    r = client.get("/api/data/fetch-step-status")
    assert r.status_code == 200
    summary = r.json()["summary"]
    bucket = summary.get(sid) or summary.get(str(sid))
    assert bucket["financials"]["status"] == "skipped"
    assert bucket["quotes"]["status"] == "error"

    r2 = client.get(f"/api/data/fetch-step-status?stock_id={sid}")
    assert r2.status_code == 200
    assert sid in r2.json()["summary"] or str(sid) in r2.json()["summary"]


def test_fetch_all_mode_incremental(client):
    r = client.post("/api/data/fetch-all?mode=incremental")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("started", "already_running", "no_stocks")
    if body["status"] == "started":
        assert body.get("mode") == "incremental"


def test_fetch_all_mode_full(client):
    r = client.post("/api/data/fetch-all?mode=full")
    assert r.status_code == 200
    body = r.json()
    if body["status"] == "started":
        assert body.get("mode") == "full"


def test_fetch_all_invalid_mode(client):
    r = client.post("/api/data/fetch-all?mode=fast")
    assert r.status_code == 422


def test_fetch_status_includes_mode(client):
    client.post("/api/data/fetch-all?mode=incremental")
    r = client.get("/api/data/fetch-status")
    assert r.status_code == 200
    data = r.json()
    assert data.get("mode") == "incremental"
    assert "stale_after_sec" in data
    assert "processed" in data
    assert "success" in data


def test_complete_job_batch_skips_per_stock_gap_sync():
    from unittest.mock import patch
    from services.fetch_job import complete_job

    with patch("services.fetch_job._maybe_sync_gaps_after_fetch") as gap:
        complete_job(1, {"status": "success", "quotes_count": 1}, sync_gaps=False)
        gap.assert_not_called()
    with patch("services.fetch_job._maybe_sync_gaps_after_fetch") as gap:
        complete_job(1, {"status": "success", "quotes_count": 1}, sync_gaps=True)
        gap.assert_called_once()
