"""共享测试 fixtures"""
from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture()
def gap_db(tmp_path, monkeypatch):
    db_path = tmp_path / "gap_test.db"
    monkeypatch.setenv("TESTING", "1")

    import config

    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(config, "latest_trading_date", lambda db_path=None: "2026-05-31")

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE stocks (
            id INTEGER PRIMARY KEY,
            code TEXT,
            name TEXT,
            is_active INTEGER DEFAULT 1
        );
        CREATE TABLE comprehensive_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            calc_date TEXT NOT NULL,
            fundamental_score REAL,
            technical_score REAL,
            sentiment_score REAL,
            composite_score REAL,
            capital_score REAL,
            policy_score REAL,
            mood_score REAL,
            val_score REAL,
            UNIQUE(stock_id, calc_date)
        );
        CREATE TABLE factor_scores (
            stock_id INTEGER,
            calc_date TEXT,
            composite_score REAL
        );
        CREATE TABLE capital_scores (
            stock_id INTEGER,
            date TEXT,
            composite_score REAL
        );
        CREATE TABLE policy_scores (
            stock_id INTEGER,
            date TEXT,
            composite_score REAL
        );
        CREATE TABLE sentiment_scores (
            stock_id INTEGER,
            date TEXT,
            composite_score REAL
        );
        CREATE TABLE valuation_scores (
            stock_id INTEGER,
            date TEXT,
            composite_score REAL
        );
        CREATE TABLE tech_analysis_cache (
            stock_id INTEGER,
            score REAL,
            created_at TEXT
        );
        CREATE TABLE stock_news (
            stock_id INTEGER,
            pub_date TEXT,
            sentiment_score REAL
        );
        CREATE TABLE stock_daily_quotes (
            stock_id INTEGER,
            trade_date TEXT
        );

        INSERT INTO stocks (id, code, name, is_active) VALUES (1, '600519', '茅台', 1);
        INSERT INTO stocks (id, code, name, is_active) VALUES (2, '000001', '平安', 1);

        INSERT INTO comprehensive_scores
            (stock_id, calc_date, technical_score, composite_score)
        VALUES (1, '2026-05-31', 65.0, 65.0);
        INSERT INTO comprehensive_scores
            (stock_id, calc_date)
        VALUES (2, '2026-05-31');

        INSERT INTO factor_scores VALUES (1, '2026-05-31', 80.0), (2, '2026-05-31', 70.0);
        INSERT INTO capital_scores VALUES (1, '2026-05-31', 75.0), (2, '2026-05-31', 72.0);
        INSERT INTO policy_scores VALUES (1, '2026-05-31', 60.0), (2, '2026-05-31', 58.0);
        INSERT INTO sentiment_scores VALUES (1, '2026-05-31', 55.0), (2, '2026-05-31', 50.0);
        INSERT INTO valuation_scores VALUES (1, '2026-05-31', 68.0), (2, '2026-05-31', 66.0);
        INSERT INTO tech_analysis_cache VALUES (1, 99.0, '2026-05-31 10:00:00');
        INSERT INTO tech_analysis_cache VALUES (2, 62.0, '2026-05-31 10:00:00');
        INSERT INTO stock_news VALUES (1, '2026-05-30', 0.8);
        """
    )
    conn.commit()
    conn.close()
    return db_path
