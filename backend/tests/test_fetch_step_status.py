"""fetch_step_status 服务层"""
import sqlite3

import pytest

from services.fetch_step_status import get_summary, record_step


@pytest.fixture()
def step_db(tmp_path, monkeypatch):
    db_path = tmp_path / "step.db"
    import config

    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE fetch_step_status (
            stock_id INTEGER NOT NULL,
            step TEXT NOT NULL,
            status TEXT NOT NULL,
            message TEXT DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (stock_id, step)
        );
        """
    )
    conn.commit()
    conn.close()
    return db_path


def test_record_and_get_summary(step_db):
    record_step(1, "financials", "skipped", "circuit_breaker")
    record_step(2, "quotes", "error", "timeout")

    summary = get_summary()
    assert 1 in summary
    assert summary[1]["financials"]["message"] == "circuit_breaker"
    assert get_summary([2])[2]["quotes"]["status"] == "error"
