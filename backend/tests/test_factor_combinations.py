"""S2 合成方案 + 回测链路"""
from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture()
def combo_db(tmp_path, monkeypatch):
    db_path = tmp_path / "combo.db"
    monkeypatch.setenv("TESTING", "1")
    import config

    path = str(db_path)
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(config, "FACTOR_MERGE_ENABLED", True)
    for mod in (
        "services.factor_combinations",
        "services.factor_factory",
        "services.factor_values_wide",
        "services.backtest_engine",
    ):
        try:
            monkeypatch.setattr(f"{mod}.DB_PATH", path)
        except AttributeError:
            pass

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE stocks (id INTEGER PRIMARY KEY, code TEXT, name TEXT, is_active INTEGER DEFAULT 1);
        CREATE TABLE factor_registry (factor_id TEXT PRIMARY KEY, name TEXT, category TEXT, formula TEXT);
        CREATE TABLE factor_values (
            stock_id INTEGER, date TEXT, factor_id TEXT, value REAL, rank INTEGER,
            PRIMARY KEY (stock_id, date, factor_id)
        );
        INSERT INTO stocks VALUES (1,'A','A',1),(2,'B','B',1),(3,'C','C',1);
        INSERT INTO factor_registry VALUES ('F010','v','t',''),('F011','v2','t','');
        INSERT INTO factor_values VALUES
            (1,'2026-05-28','F010',10,1),(2,'2026-05-28','F010',20,2),(3,'2026-05-28','F010',30,3),
            (1,'2026-05-28','F011',1,1),(2,'2026-05-28','F011',2,2),(3,'2026-05-28','F011',3,3),
            (1,'2026-05-29','F010',11,1),(2,'2026-05-29','F010',21,2),(3,'2026-05-29','F010',31,3),
            (1,'2026-05-29','F011',2,1),(2,'2026-05-29','F011',3,2),(3,'2026-05-29','F011',4,3);
        CREATE TABLE stock_daily_quotes (
            stock_id INTEGER, trade_date TEXT, close REAL, volume REAL,
            change_pct REAL, is_suspended INTEGER DEFAULT 0
        );
        INSERT INTO stock_daily_quotes VALUES
            (1,'2026-05-28',10,1e6,0,0),(2,'2026-05-28',10,1e6,0,0),(3,'2026-05-28',10,1e6,0,0),
            (1,'2026-05-29',10.5,1e6,5,0),(2,'2026-05-29',10.2,1e6,2,0),(3,'2026-05-29',9.8,1e6,-2,0);
        """
    )
    conn.commit()
    conn.close()
    return db_path


def test_create_and_materialize(combo_db, monkeypatch):
    monkeypatch.setattr("services.factor_combinations.FACTOR_MERGE_ENABLED", True)
    import importlib
    import services.factor_combinations as fc

    importlib.reload(fc)
    r = fc.create_combination("test_combo", ["F010", "F011"], weight_method="equal", materialize=True)
    assert "error" not in r
    assert r.get("output_factor_id") or r.get("materialize", {}).get("output_factor_id")
    out = r.get("output_factor_id") or r["materialize"]["output_factor_id"]
    snap = fc.load_factor_score_snap(out, "2026-05-28", "2026-05-29")
    assert len(snap) >= 1


def test_trade_allowed_limit():
    from services.trading_rules import trade_allowed

    assert trade_allowed("buy", {"volume": 1000, "change_pct": 10.0}) is False
    assert trade_allowed("sell", {"volume": 1000, "change_pct": -10.0}) is False
    assert trade_allowed("buy", {"volume": 0, "change_pct": 0}) is False
    assert trade_allowed("buy", {"volume": 1000, "change_pct": 1.0}) is True


def test_backtest_factor_combination(combo_db, monkeypatch):
    monkeypatch.setattr("services.factor_combinations.FACTOR_MERGE_ENABLED", True)
    import importlib
    import services.factor_combinations as fc
    import services.backtest_engine as be

    importlib.reload(fc)
    importlib.reload(be)
    created = fc.create_combination("bt_combo", ["F010", "F011"], materialize=True)
    cid = created["id"]
    combo = fc.get_combination(cid)
    assert combo and combo.get("output_factor_id")
    snap = fc.load_factor_score_snap(combo["output_factor_id"], "2026-05-28", "2026-05-29")
    assert len(snap) >= 1
    r = be.run_backtest(
        days=2,
        top_n=2,
        lookback=1,
        strategy="factor_combination",
        combination_id=cid,
        min_score=0,
        rebalance="daily",
    )
    assert "error" in r or r.get("params", {}).get("combination_id") == cid
