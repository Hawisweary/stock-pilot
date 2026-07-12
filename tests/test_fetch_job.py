"""抓取任务与 partial 状态 — 单元/集成测试（mock 外部数据源）"""
import os
import sqlite3
import sys
from unittest.mock import patch

import pytest

ROOT = os.path.dirname(os.path.dirname(__file__))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)

os.environ.setdefault("TESTING", "1")


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    import config

    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE stocks (
            id INTEGER PRIMARY KEY, code TEXT, name TEXT, market TEXT,
            industry TEXT, industry_sw TEXT, is_active INTEGER DEFAULT 1
        )
        """
    )
    conn.execute(
        "INSERT INTO stocks (id, code, name, market) VALUES (1, '600519', '茅台', 'A')"
    )
    conn.execute(
        """
        CREATE TABLE data_fetch_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER, data_type TEXT, status TEXT,
            records_count INTEGER, error_message TEXT, duration_ms INTEGER,
            fetch_time TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE fetch_jobs (
            stock_id INTEGER PRIMARY KEY,
            status TEXT, running INTEGER,
            quotes INTEGER, financials INTEGER, indicators INTEGER,
            errors_json TEXT, error TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()
    conn.close()

    import services.fetch_job as fj

    monkeypatch.setattr(fj, "DB_PATH", str(db_path))
    return str(db_path)


def test_partial_status_on_mixed_result(temp_db):
    import services.fetch_job as fj

    def fake_fetch(stock_id, code, market):
        return {
            "quotes_count": 10,
            "financials_count": 0,
            "indicators_count": 0,
            "status": "partial",
            "errors": [{"step": "financials", "message": "timeout"}],
        }

    with patch.object(fj, "sync_fetch_one", side_effect=fake_fetch):
        fj.start_job(1)
        fj.complete_job(1, fake_fetch(1, "600519", "A"))

    status = fj.status_payload(1)
    assert status["status"] == "partial"
    assert status["quotes"] == 10
    assert len(status["errors"]) == 1


def test_fetch_api_health_not_blocked(temp_db, monkeypatch):
    """抓取进行中 health 仍应快速响应（逻辑测试：status 可读）"""
    import services.fetch_job as fj

    fj.start_job(1)
    assert fj.is_running(1) is True
    payload = fj.status_payload(1)
    assert payload["running"] is True
    fj.complete_job(
        1,
        {"quotes_count": 1, "financials_count": 0, "indicators_count": 0, "status": "success", "errors": []},
    )
    assert fj.is_running(1) is False
