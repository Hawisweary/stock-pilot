"""portfolio_svc 核心服务测试 — SEC-OPS P1-7"""
from __future__ import annotations

import sqlite3
import sys
import os
import tempfile
from datetime import date

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def patch_db(tmp_path, monkeypatch):
    # 屏蔽实时行情，避免测试依赖网络且触发涨停锁定
    monkeypatch.setattr(
        "services.trade_pricing._fetch_realtime", lambda code: None
    )
    """每个测试使用独立临时文件 DB，保证隔离"""
    db_file = str(tmp_path / "test.db")
    import config
    monkeypatch.setattr(config, "DB_PATH", db_file)

    # 重新导入 db_util 和 portfolio_svc 使用新 DB_PATH
    import importlib
    import db_util
    import services.portfolio_svc as svc
    importlib.reload(db_util)
    importlib.reload(svc)

    # 建表 + 插入测试用股票和行情
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    svc._ensure_tables(conn)
    today = date.today().strftime("%Y-%m-%d")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            name TEXT,
            is_active INTEGER DEFAULT 1,
            market TEXT DEFAULT 'SZ'
        );
        CREATE TABLE IF NOT EXISTS stock_daily_quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            trade_date TEXT NOT NULL,
            open REAL, close REAL, high REAL, low REAL,
            volume REAL, change_pct REAL,
            UNIQUE(stock_id, trade_date)
        );
        CREATE TABLE IF NOT EXISTS trade_calendar (
            cal_date TEXT PRIMARY KEY,
            is_open INTEGER NOT NULL
        );
    """)
    conn.execute(
        "INSERT OR REPLACE INTO trade_calendar (cal_date, is_open) VALUES (?,1)",
        (today,),
    )
    for code, price in [
        ("000001", 10.0), ("000002", 5.0), ("000003", 100.0),
        ("000004", 12.0), ("000005", 8.0), ("000006", 15.0),
        ("000007", 20.0), ("000008", 25.0), ("000009", 30.0),
    ]:
        conn.execute(
            "INSERT OR IGNORE INTO stocks (code, name, is_active) VALUES (?,?,1)",
            (code, f"股票{code}"),
        )
        sid = conn.execute("SELECT id FROM stocks WHERE code=?", (code,)).fetchone()[0]
        conn.execute(
            "INSERT OR REPLACE INTO stock_daily_quotes "
            "(stock_id, trade_date, open, close, high, low, volume, change_pct) VALUES (?,?,?,?,?,?,?,?)",
            (sid, today, price, price, price * 1.02, price * 0.98, 1_000_000, 0.0),
        )
    conn.commit()
    conn.close()

    from services.trade_calendar import invalidate_cache
    invalidate_cache()

    yield svc  # 测试函数通过参数名 `svc` 或直接用下面的 helper


@pytest.fixture
def svc(patch_db):
    return patch_db


@pytest.fixture
def pf(svc):
    return svc.create_portfolio("default", initial_cash=200_000)


# ── 组合 CRUD ──────────────────────────────────────────────────

def test_create_portfolio_defaults(svc):
    p = svc.create_portfolio("p1")
    assert p["name"] == "p1"
    assert p["cash"] == 100_000


def test_create_portfolio_custom_cash(svc):
    p = svc.create_portfolio("rich", initial_cash=500_000)
    assert p["cash"] == 500_000


def test_get_portfolios_contains_created(svc):
    svc.create_portfolio("unique_name_xyz")
    names = [p["name"] for p in svc.get_portfolios()]
    assert "unique_name_xyz" in names


def test_rename_portfolio(svc):
    p = svc.create_portfolio("old")
    svc.rename_portfolio(p["id"], "new")
    # 验证 DB 里已改名
    names = [p["name"] for p in svc.get_portfolios()]
    assert "new" in names


def test_delete_portfolio(svc):
    p = svc.create_portfolio("to_del")
    pid = p["id"]
    svc.delete_portfolio(pid)
    assert all(x["id"] != pid for x in svc.get_portfolios())


def test_get_portfolio_structure(svc, pf):
    detail = svc.get_portfolio(pf["id"])
    assert "cash" in detail
    assert "positions" in detail


# ── 买入 ──────────────────────────────────────────────────────

def test_trade_buy_reduces_cash(svc, pf):
    res = svc.trade(pf["id"], "000001", "buy", 100)
    assert "error" not in res, res
    detail = svc.get_portfolio(pf["id"])
    assert detail["cash"] < 200_000


def test_trade_buy_creates_position(svc, pf):
    svc.trade(pf["id"], "000002", "buy", 100)
    codes = [p["code"] for p in svc.get_portfolio(pf["id"])["positions"]]
    assert "000002" in codes


def test_trade_buy_accumulates_shares(svc, pf):
    svc.trade(pf["id"], "000001", "buy", 100)
    svc.trade(pf["id"], "000001", "buy", 200)
    pos = [p for p in svc.get_portfolio(pf["id"])["positions"] if p["code"] == "000001"]
    assert pos[0]["shares"] == 300


def test_trade_buy_insufficient_cash(svc):
    p = svc.create_portfolio("poor", initial_cash=100)
    res = svc.trade(p["id"], "000003", "buy", 1000)
    assert "error" in res


def test_trade_buy_unknown_stock(svc, pf):
    res = svc.trade(pf["id"], "999999", "buy", 100)
    assert "error" in res


# ── 卖出 ──────────────────────────────────────────────────────

def test_trade_sell_increases_cash(svc, pf):
    svc.trade(pf["id"], "000004", "buy", 200)
    cash_before = svc.get_portfolio(pf["id"])["cash"]
    res = svc.trade(pf["id"], "000004", "sell", 100, apply_t1=False)
    assert "error" not in res, res
    assert svc.get_portfolio(pf["id"])["cash"] > cash_before


def test_trade_sell_clears_position(svc, pf):
    svc.trade(pf["id"], "000005", "buy", 100)
    svc.trade(pf["id"], "000005", "sell", 100, apply_t1=False)
    codes = [p["code"] for p in svc.get_portfolio(pf["id"])["positions"]]
    assert "000005" not in codes


def test_trade_sell_over_held(svc, pf):
    svc.trade(pf["id"], "000001", "buy", 100)
    res = svc.trade(pf["id"], "000001", "sell", 9999, apply_t1=False)
    assert "error" in res


def test_trade_sell_unknown_stock(svc, pf):
    res = svc.trade(pf["id"], "999999", "sell", 100)
    assert "error" in res


# ── T+1 ─────────────────────────────────────────────────────

def test_trade_sell_t1_blocks_same_day(svc, pf):
    svc.trade(pf["id"], "000006", "buy", 100)
    res = svc.trade(pf["id"], "000006", "sell", 100, apply_t1=True)
    assert "error" in res


# ── assert_lots_positions_sync ───────────────────────────────

def test_sync_passes_after_trade(svc, pf):
    svc.trade(pf["id"], "000007", "buy", 100)
    svc.assert_lots_positions_sync(pf["id"])  # 不抛出即通过


def test_sync_fails_on_drift(svc, pf, tmp_path):
    import config
    svc.trade(pf["id"], "000008", "buy", 100)
    # 直接篡改 positions
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute(
        "UPDATE portfolio_positions SET shares=9999 WHERE portfolio_id=?", (pf["id"],)
    )
    conn.commit()
    conn.close()
    with pytest.raises(AssertionError):
        svc.assert_lots_positions_sync(pf["id"])


# ── delete 原子性 ─────────────────────────────────────────────

def test_delete_clears_lots_and_journal(svc, pf, tmp_path):
    import config
    pid = pf["id"]
    svc.trade(pid, "000009", "buy", 100)
    svc.delete_portfolio(pid)
    conn = sqlite3.connect(config.DB_PATH)
    lots = conn.execute(
        "SELECT COUNT(*) FROM portfolio_lots WHERE portfolio_id=?", (pid,)
    ).fetchone()[0]
    journal = conn.execute(
        "SELECT COUNT(*) FROM trade_journal WHERE portfolio_id=?", (pid,)
    ).fetchone()[0]
    conn.close()
    assert lots == 0
    assert journal == 0


# ── calc_total_value ─────────────────────────────────────────

def test_calc_total_value_non_negative(svc, pf):
    tv = svc.calc_total_value(pf["id"])
    assert tv >= 0


def test_asymmetric_rebalance_keeps_in_target(svc, pf, monkeypatch, tmp_path):
    """仍在 Top N 的持仓不应被清仓，仅掉出名单才全卖。"""
    import config
    from datetime import timedelta

    monkeypatch.setattr(
        "services.portfolio_svc.select_top_n_dicts",
        lambda **kwargs: (
            [{"code": "000001", "score": 90.0, "name": "股票000001", "stock_id": 1}],
            None,
        ),
    )
    svc.trade(pf["id"], "000001", "buy", 1000)
    svc.trade(pf["id"], "000002", "buy", 100)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute(
        "UPDATE portfolio_lots SET buy_date=? WHERE portfolio_id=?",
        (yesterday, pf["id"]),
    )
    conn.commit()
    conn.close()

    r = svc.build_from_top_n(pf["id"], top_n=1, min_score=50)
    assert "error" not in r, r
    codes = [p["code"] for p in svc.get_portfolio(pf["id"])["positions"]]
    assert "000001" in codes
    assert "000002" not in codes
    assert any(s["code"] == "000002" for s in r["sold"])
    assert r.get("rebalance_mode") == "asymmetric"
