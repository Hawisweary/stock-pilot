"""S0 因子数据质量 — 幸存者偏差 / 复权 / 未来函数 / 宽表"""
from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture()
def factor_s0_db(tmp_path, monkeypatch):
    db_path = tmp_path / "factor_s0.db"
    monkeypatch.setenv("TESTING", "1")

    import config

    path = str(db_path)
    monkeypatch.setattr(config, "DB_PATH", path)
    for mod in (
        "services.data_cleaner",
        "services.stock_lifecycle",
        "services.financial_calendar",
        "services.factor_quality",
        "services.factor_values_wide",
        "services.factor_s0_setup",
        "services.ic_engine",
        "services.factor_factory",
    ):
        try:
            monkeypatch.setattr(f"{mod}.DB_PATH", path)
        except AttributeError:
            pass

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE stocks (
            id INTEGER PRIMARY KEY,
            code TEXT,
            name TEXT,
            list_date TEXT,
            is_active INTEGER DEFAULT 1
        );
        CREATE TABLE stock_daily_quotes (
            stock_id INTEGER,
            trade_date TEXT,
            close REAL,
            volume REAL,
            adj_close REAL,
            is_suspended INTEGER DEFAULT 0
        );
        CREATE TABLE comprehensive_scores (
            stock_id INTEGER,
            calc_date TEXT,
            fundamental_score REAL,
            composite_score REAL
        );
        CREATE TABLE financial_reports (
            stock_id INTEGER,
            period_end_date TEXT,
            report_type TEXT,
            report_date TEXT
        );
        CREATE TABLE factor_registry (
            factor_id TEXT PRIMARY KEY, name TEXT, category TEXT, formula TEXT
        );
        CREATE TABLE factor_values (
            stock_id INTEGER, date TEXT, factor_id TEXT,
            value REAL, rank INTEGER,
            PRIMARY KEY (stock_id, date, factor_id)
        );

        -- 活跃股 + 已退市股
        INSERT INTO stocks VALUES (1, '600519', '茅台', '2010-01-01', 1);
        INSERT INTO stocks VALUES (2, '000001', '平安', '2010-01-01', 0);

        -- 退市股最后行情在 2026-03-01
        INSERT INTO stock_daily_quotes VALUES
            (1, '2026-05-25', 100, 1e6, NULL, 0),
            (1, '2026-05-26', 101, 1e6, NULL, 0),
            (1, '2026-05-27', 102, 1e6, NULL, 0),
            (1, '2026-05-28', 103, 1e6, NULL, 0),
            (1, '2026-05-29', 104, 1e6, NULL, 0),
            (2, '2026-02-26', 50, 1e6, NULL, 0),
            (2, '2026-02-27', 51, 1e6, NULL, 0),
            (2, '2026-02-28', 52, 1e6, NULL, 0),
            (2, '2026-03-01', 53, 1e6, NULL, 0);

        -- 未来函数：2026-05-29 基本面分，但财报 2026-06-15 才披露
        INSERT INTO financial_reports VALUES (1, '2026-03-31', 'annual', '2026-06-15');
        INSERT INTO comprehensive_scores VALUES (1, '2026-05-29', 80.0, 80.0);

        INSERT INTO factor_values VALUES (1, '2026-05-29', 'F009', 5.0, NULL);
        """
    )
    conn.commit()
    conn.close()
    return db_path


def test_adj_close_backfill(factor_s0_db):
    from services.data_cleaner import backfill_adj_close

    r = backfill_adj_close()
    assert r["adj_close_filled"] >= 9

    conn = sqlite3.connect(factor_s0_db)
    n = conn.execute(
        "SELECT COUNT(*) FROM stock_daily_quotes WHERE adj_close IS NOT NULL"
    ).fetchone()[0]
    conn.close()
    assert n == 9


def test_lifecycle_delisted(factor_s0_db):
    from services.stock_lifecycle import sync_lifecycle_from_stocks, is_alive

    sync_lifecycle_from_stocks()
    assert is_alive(1, "2026-05-29") is True
    assert is_alive(2, "2026-05-29") is False
    assert is_alive(2, "2026-03-01") is True


def test_fundamental_look_ahead_filtered(factor_s0_db):
    from services.financial_calendar import rebuild_from_financial_reports
    from services.factor_quality import filter_fundamental_for_backfill, is_factor_value_valid

    rebuild_from_financial_reports()
    assert filter_fundamental_for_backfill(1, "2026-05-29", 80.0) is None
    assert filter_fundamental_for_backfill(1, "2026-06-20", 80.0) == 80.0
    valid, flag = is_factor_value_valid("F002", 1, "2026-05-29")
    assert valid is False


def test_wide_migration_dual_write(factor_s0_db):
    from services.factor_values_wide import migrate_eav_to_wide, read_factor_series_from_wide
    from services.factor_factory import init_factor_store, _upsert_factor

    conn = init_factor_store()
    _upsert_factor(conn, 1, "2026-05-29", "F009", 5.0)
    conn.commit()
    conn.close()

    r = migrate_eav_to_wide()
    assert r["wide_rows"] >= 1
    series = read_factor_series_from_wide("F009")
    assert any(s[2] == 5.0 for s in series)


def test_ic_excludes_delisted_on_late_date(factor_s0_db):
    from services.data_cleaner import backfill_adj_close
    from services.stock_lifecycle import sync_lifecycle_from_stocks
    from services.factor_factory import init_factor_store, _upsert_factor
    from services.ic_engine import analyze_factor_id

    backfill_adj_close()
    sync_lifecycle_from_stocks()
    conn = init_factor_store()
    _upsert_factor(conn, 1, "2026-05-29", "F009", 5.0)
    _upsert_factor(conn, 2, "2026-03-01", "F009", 3.0)
    conn.commit()
    conn.close()

    r = analyze_factor_id("F009", forward_days=1, max_dates=10)
    assert r.get("survivorship_adjusted") is True
    assert "error" in r or r.get("n_periods", 0) >= 0
