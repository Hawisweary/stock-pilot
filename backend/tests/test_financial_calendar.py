"""财报日历：未来披露推算与 API 路径"""
from __future__ import annotations

import sqlite3
from datetime import date

import pytest


@pytest.fixture
def cal_db(tmp_path, monkeypatch):
    db = tmp_path / "cal.db"
    monkeypatch.setenv("AFR_DB_PATH", str(db))
    import config

    monkeypatch.setattr(config, "DB_PATH", str(db))
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE stocks (id INTEGER PRIMARY KEY, code TEXT, name TEXT, is_active INTEGER DEFAULT 1);
        CREATE TABLE financial_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER, period_end_date TEXT, report_type TEXT, report_date TEXT
        );
        INSERT INTO stocks (id, code, name) VALUES (1, '600519', '贵州茅台');
        INSERT INTO financial_reports (stock_id, period_end_date, report_type, report_date)
        VALUES (1, '2026-03-31', 'q1', '2026-04-25');
        """
    )
    conn.commit()
    conn.close()
    return db


def test_project_upcoming_after_q1(cal_db, monkeypatch):
    from services import financial_calendar as fc

    monkeypatch.setattr(fc, "date", __import__("datetime").date)

    conn = sqlite3.connect(cal_db)
    fc.ensure_tables(conn)
    fc.rebuild_from_financial_reports(conn)
    out = fc.project_upcoming_disclosures(conn, ahead_days=200)
    conn.commit()

    row = conn.execute(
        """SELECT period_end_date, report_type, disclosure_date, source
           FROM financial_calendar
           WHERE stock_id=1 AND disclosure_date >= date('now')
           ORDER BY disclosure_date LIMIT 1"""
    ).fetchone()
    conn.close()

    assert out["projected"] >= 1
    assert row is not None
    assert row[0] == "2026-06-30"
    assert row[1] == "q2"
    assert row[2] == "2026-08-31"
    assert row[3] == "projected_statutory"
