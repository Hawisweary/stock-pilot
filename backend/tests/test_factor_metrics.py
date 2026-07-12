"""S1 因子评估指标测试"""
from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture()
def metrics_db(tmp_path, monkeypatch):
    db_path = tmp_path / "metrics.db"
    monkeypatch.setenv("TESTING", "1")
    import config

    path = str(db_path)
    monkeypatch.setattr(config, "DB_PATH", path)
    for mod in (
        "services.ic_engine",
        "services.factor_metrics",
        "services.factor_factory",
        "services.stock_lifecycle",
        "services.data_cleaner",
    ):
        try:
            monkeypatch.setattr(f"{mod}.DB_PATH", path)
        except AttributeError:
            pass

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE stocks (id INTEGER PRIMARY KEY, code TEXT, list_date TEXT, is_active INTEGER DEFAULT 1);
        CREATE TABLE stock_lifecycle (stock_id INTEGER PRIMARY KEY, code TEXT, list_date TEXT, delist_date TEXT, source TEXT, updated_at TEXT);
        CREATE TABLE stock_daily_quotes (
            stock_id INTEGER, trade_date TEXT, close REAL, volume REAL, adj_close REAL, is_suspended INTEGER DEFAULT 0
        );
        CREATE TABLE factor_registry (factor_id TEXT PRIMARY KEY, name TEXT, category TEXT, formula TEXT);
        CREATE TABLE factor_values (
            stock_id INTEGER, date TEXT, factor_id TEXT, value REAL, rank INTEGER,
            PRIMARY KEY (stock_id, date, factor_id)
        );

        INSERT INTO stocks (id, code, list_date) VALUES (1,'A','2020-01-01'),(2,'B','2020-01-01'),(3,'C','2020-01-01'),(4,'D','2020-01-01'),(5,'E','2020-01-01');
        INSERT INTO stock_lifecycle SELECT id, code, list_date, NULL, 'test', datetime('now') FROM stocks;

        -- 两个交易日，因子单调递增应对应收益递增
        INSERT INTO stock_daily_quotes VALUES
            (1,'2026-05-28',10,1e6,10,0),(2,'2026-05-28',10,1e6,10,0),(3,'2026-05-28',10,1e6,10,0),(4,'2026-05-28',10,1e6,10,0),(5,'2026-05-28',10,1e6,10,0),
            (1,'2026-05-29',11,1e6,11,0),(2,'2026-05-29',10.5,1e6,10.5,0),(3,'2026-05-29',10,1e6,10,0),(4,'2026-05-29',9.5,1e6,9.5,0),(5,'2026-05-29',9,1e6,9,0);

        INSERT INTO factor_values VALUES
            (5,'2026-05-28','F009',1,1),(4,'2026-05-28','F009',2,2),(3,'2026-05-28','F009',3,3),(2,'2026-05-28','F009',4,4),(1,'2026-05-28','F009',5,5);
        """
    )
    conn.commit()
    conn.close()

    import importlib
    import services.ic_engine as ic_engine
    import services.factor_metrics as factor_metrics

    importlib.reload(ic_engine)
    importlib.reload(factor_metrics)
    return db_path


def test_ic_ttest_significant():
    from services.factor_metrics import ic_ttest

    ics = [0.05, 0.04, 0.06, 0.05, 0.07, 0.04, 0.05, 0.06, 0.05, 0.05]
    r = ic_ttest(ics)
    assert r["p_value"] is not None
    assert r["p_value"] < 0.05
    assert r["significance"] in ("*", "**", "***")


def test_monotonicity_positive(metrics_db):
    import importlib
    import services.factor_metrics as factor_metrics

    importlib.reload(factor_metrics)
    r = factor_metrics.analyze_factor_metrics("F009", forward_days=1, max_dates=10)
    mono = r.get("monotonicity", {})
    assert mono.get("spearman") is not None
    assert mono["spearman"] > 0.5


def test_turnover_and_long_short(metrics_db):
    import importlib
    import services.factor_metrics as factor_metrics

    importlib.reload(factor_metrics)
    r = factor_metrics.analyze_factor_metrics("F009", forward_days=1, max_dates=10)
    assert r.get("turnover") is not None
    assert r.get("long_short", {}).get("n_periods", 0) >= 1


def test_extended_analysis_api_shape(metrics_db):
    from services.factor_factory import factor_extended_analysis

    r = factor_extended_analysis("F009", forward_days=1)
    assert "monotonicity" in r
    assert "ic_significance" in r
    assert "long_short" in r
