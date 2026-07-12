"""factor_engine smoke tests — SEC-OPS P1-7

测试内部纯函数，不依赖数据库，避免外部数据拉取。
"""
from __future__ import annotations

import sqlite3
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def patch_db(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "fe.db"))
    monkeypatch.setattr(config, "FACTOR_BENCHMARK_DEFAULT", "watchlist")
    import importlib, db_util
    importlib.reload(db_util)


def _make_engine(db_path: str):
    import config
    config.DB_PATH = db_path
    from services.factor_engine import FactorEngine
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS stocks (
            id INTEGER PRIMARY KEY, code TEXT, name TEXT,
            is_active INTEGER DEFAULT 1,
            industry TEXT DEFAULT 'IT', industry_sw TEXT DEFAULT '电子'
        );
        CREATE TABLE IF NOT EXISTS financial_reports (
            id INTEGER PRIMARY KEY, stock_id INTEGER,
            period_end_date TEXT, report_type TEXT,
            revenue REAL, net_profit REAL, eps REAL,
            roe REAL, gross_margin REAL, net_margin REAL,
            roic REAL, debt_ratio REAL, operating_cf REAL
        );
        CREATE TABLE IF NOT EXISTS factor_weights (
            id INTEGER PRIMARY KEY,
            quality REAL, growth REAL, value REAL, momentum REAL, risk REAL,
            is_active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS comprehensive_scores (
            stock_id INTEGER, calc_date TEXT, fundamental_score REAL,
            quality_score REAL, growth_score REAL, PRIMARY KEY(stock_id, calc_date)
        );
        CREATE TABLE IF NOT EXISTS factor_scores (
            id INTEGER PRIMARY KEY,
            stock_id INTEGER, calc_date TEXT, composite REAL,
            quality REAL, growth REAL, value REAL, momentum REAL, risk REAL,
            detail_json TEXT, benchmark_mode TEXT
        );
    """)
    conn.commit()
    return FactorEngine(conn), conn


# ── _is_positive_number ──────────────────────────────────────

def test_is_positive_number_true():
    from services.factor_engine import _is_positive_number
    assert _is_positive_number(1.0) is True
    assert _is_positive_number(0.001) is True


def test_is_positive_number_false():
    from services.factor_engine import _is_positive_number
    assert _is_positive_number(None) is False
    assert _is_positive_number(0) is False
    assert _is_positive_number(-1) is False
    assert _is_positive_number(float("nan")) is False


# ── FACTOR_META 完整性 ───────────────────────────────────────

def test_factor_meta_weights_sum_to_one():
    from services.factor_engine import FACTOR_META
    total = sum(v["weight_default"] for v in FACTOR_META.values())
    assert abs(total - 1.0) < 1e-6, f"权重之和应为 1，实际={total}"


def test_factor_meta_keys():
    from services.factor_engine import FACTOR_META
    assert set(FACTOR_META.keys()) == {"quality", "growth", "value", "momentum", "risk"}


# ── _calc_quality score range ────────────────────────────────

def test_calc_quality_score_in_range(tmp_path):
    import config
    config.DB_PATH = str(tmp_path / "fe2.db")
    engine, conn = _make_engine(config.DB_PATH)
    m = {"roe": 0.15, "gross_margin": 0.40, "net_margin": 0.12, "roic": 0.10}
    peers = {}  # 无同行 → 均用默认 50 分
    detail, score = engine._calc_quality(m, peers)
    assert 0 <= score <= 100
    assert "sub_scores" in detail


# ── _calc_risk score range ───────────────────────────────────

def test_calc_risk_score_low_debt(tmp_path):
    import config
    config.DB_PATH = str(tmp_path / "fe3.db")
    engine, conn = _make_engine(config.DB_PATH)
    m = {"debt_ratio": 0.2, "_financials": [
        {"operating_cf": 500_000, "net_profit": 400_000}
    ]}
    _, score = engine._calc_risk(m, {})
    assert 0 <= score <= 100


# ── _load_weights returns valid dict ─────────────────────────

def test_load_weights_returns_all_factors(tmp_path):
    import config
    config.DB_PATH = str(tmp_path / "fe4.db")
    engine, conn = _make_engine(config.DB_PATH)
    weights = engine._load_weights()
    assert set(weights.keys()) == {"quality", "growth", "value", "momentum", "risk"}
    total = sum(weights.values())
    assert abs(total - 1.0) < 1e-6


# ── _peer_metrics 空池不崩溃 ─────────────────────────────────

def test_peer_metrics_empty_pool(tmp_path):
    import config
    config.DB_PATH = str(tmp_path / "fe5.db")
    engine, conn = _make_engine(config.DB_PATH)
    result = engine._peer_metrics(1, {}, {})
    assert isinstance(result, dict)
