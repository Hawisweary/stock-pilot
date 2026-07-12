"""S3 增量计算 + 中性化 + 正交化"""
from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture()
def s3_db(tmp_path, monkeypatch):
    db_path = tmp_path / "s3.db"
    monkeypatch.setenv("TESTING", "1")
    import config

    path = str(db_path)
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(config, "FACTOR_NEUTRALIZE_ENABLED", True)
    for mod in (
        "services.factor_incremental",
        "services.factor_neutralize",
        "services.factor_orthogonal",
        "services.factor_factory",
        "services.factor_values_wide",
        "services.factor_quality",
        "services.financial_calendar",
    ):
        try:
            monkeypatch.setattr(f"{mod}.DB_PATH", path)
        except AttributeError:
            pass

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE stocks (
            id INTEGER PRIMARY KEY, code TEXT, name TEXT, is_active INTEGER DEFAULT 1,
            industry_sw TEXT, industry TEXT
        );
        CREATE TABLE factor_registry (factor_id TEXT PRIMARY KEY, name TEXT, category TEXT, formula TEXT);
        CREATE TABLE factor_values (
            stock_id INTEGER, date TEXT, factor_id TEXT, value REAL, rank INTEGER,
            PRIMARY KEY (stock_id, date, factor_id)
        );
        CREATE TABLE comprehensive_scores (
            stock_id INTEGER, calc_date TEXT, composite_score REAL, fundamental_score REAL,
            technical_score REAL, sentiment_score REAL, capital_score REAL,
            policy_score REAL, mood_score REAL, val_score REAL
        );
        CREATE TABLE stock_daily_quotes (
            stock_id INTEGER, trade_date TEXT, close REAL, adj_close REAL,
            volume REAL, change_pct REAL, is_suspended INTEGER DEFAULT 0
        );
        CREATE TABLE valuation_snapshots (
            stock_id INTEGER, as_of_date TEXT, market_cap REAL
        );
        CREATE TABLE financial_calendar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            period_end_date TEXT NOT NULL,
            report_type TEXT DEFAULT 'annual',
            disclosure_date TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'conservative+45',
            UNIQUE(stock_id, period_end_date, report_type)
        );
        INSERT INTO stocks VALUES
            (1,'A','A',1,'银行','银行'),
            (2,'B','B',1,'银行','银行'),
            (3,'C','C',1,'医药','医药'),
            (4,'D','D',1,'医药','医药'),
            (5,'E','E',1,'电子','电子'),
            (6,'F','F',1,'电子','电子'),
            (7,'G','G',1,'食品','食品'),
            (8,'H','H',1,'食品','食品');
        INSERT INTO factor_registry VALUES ('F009','mom','t',''),('F010','vol','t','');
        INSERT INTO comprehensive_scores VALUES
            (1,'2026-05-29',80,70,60,50,40,30,20,10),
            (2,'2026-05-29',75,65,55,45,35,25,15,5),
            (3,'2026-05-29',70,60,50,40,30,20,10,8),
            (4,'2026-05-29',65,55,45,35,25,15,8,6),
            (5,'2026-05-29',60,50,40,30,20,10,6,4),
            (6,'2026-05-29',55,45,35,25,15,8,4,2),
            (7,'2026-05-29',50,40,30,20,10,6,2,1),
            (8,'2026-05-29',45,35,25,15,8,4,1,0);
        INSERT INTO stock_daily_quotes
        SELECT id, '2026-05-29', 10.0, 10.0, 1e6, 0, 0 FROM stocks;
        INSERT INTO stock_daily_quotes
        SELECT id, '2026-05-28', 9.5, 9.5, 1e6, 0, 0 FROM stocks;
        """
    )
    # 30 days history for technical factors
    for sid in range(1, 9):
        for d in range(30):
            conn.execute(
                """INSERT INTO stock_daily_quotes
                   (stock_id, trade_date, close, adj_close, volume, change_pct)
                   VALUES (?,?,?,?,?,?)""",
                (sid, f"2026-05-{29-d:02d}" if 29 - d >= 1 else f"2026-04-{30 + (29-d):02d}", 10 + d * 0.01, 10 + d * 0.01, 1e6, 0),
            )
    conn.commit()
    conn.close()
    return db_path


def test_incremental_compute(s3_db):
    import importlib
    import services.factor_incremental as fi

    importlib.reload(fi)
    r = fi.compute_factors_incremental()
    assert "error" not in r
    assert r["mode"] == "incremental"
    assert r["cells_written"] > 0

    conn = sqlite3.connect(s3_db)
    n = conn.execute("SELECT COUNT(*) FROM factor_values WHERE date='2026-05-29'").fetchone()[0]
    log = conn.execute("SELECT mode FROM factor_compute_log ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert n > 0
    assert log[0] == "incremental"


def test_neutralize_factor(s3_db):
    conn = sqlite3.connect(s3_db)
    for sid in range(1, 9):
        conn.execute(
            "INSERT INTO factor_values VALUES (?, '2026-05-29', 'F009', ?, NULL)",
            (sid, float(sid * 10)),
        )
        conn.execute(
            "INSERT INTO valuation_snapshots VALUES (?, '2026-05-29', ?)",
            (sid, float(sid * 1e9)),
        )
    conn.commit()
    conn.close()

    import importlib
    import services.factor_neutralize as fn

    importlib.reload(fn)
    r = fn.neutralize_factor("F009", max_dates=1)
    assert r["output_factor_id"] == "F009_N"
    assert r["cells_written"] >= 8

    conn = sqlite3.connect(s3_db)
    rows = conn.execute(
        "SELECT value FROM factor_values WHERE factor_id='F009_N' AND date='2026-05-29'"
    ).fetchall()
    conn.close()
    assert len(rows) == 8


def test_orthogonalize_factors(s3_db):
    conn = sqlite3.connect(s3_db)
    for sid in range(1, 9):
        conn.execute("INSERT INTO factor_values VALUES (?, '2026-05-29', 'F009', ?, NULL)", (sid, float(sid)))
        conn.execute("INSERT INTO factor_values VALUES (?, '2026-05-29', 'F010', ?, NULL)", (sid, float(sid * 2)))
    conn.commit()
    conn.close()

    import importlib
    import services.factor_orthogonal as fo

    importlib.reload(fo)
    r = fo.orthogonalize_factors(["F009", "F010"], max_dates=1)
    assert len(r["output_factors"]) == 2
    assert r["cells_written"] >= 16


def test_gram_schmidt_orthogonal():
    from services.factor_orthogonal import _gram_schmidt_columns

    m = [[1.0, 1.0], [2.0, 2.0], [0.0, 1.0]]
    q = _gram_schmidt_columns(m)
    dot = sum(q[i][0] * q[i][1] for i in range(3))
    assert abs(dot) < 1e-6
