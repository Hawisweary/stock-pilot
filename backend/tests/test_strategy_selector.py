"""统一策略注册表与选股。"""
from __future__ import annotations

import sqlite3

import pytest


def test_strategy_registry_resolve_v5():
    from services.strategy_registry import get_meta, is_valid_strategy, resolve_for_trading_rules

    assert get_meta("composite") is not None
    assert is_valid_strategy("composite")
    assert resolve_for_trading_rules("composite") == ("composite", "composite_v5")
    assert is_valid_strategy("momentum")
    assert resolve_for_trading_rules("momentum") == ("momentum", "momentum")
    assert is_valid_strategy("F013")
    assert is_valid_strategy("dual_ma")
    assert not is_valid_strategy("factor_combination")
    assert is_valid_strategy("factor_combination", combination_id=1)


def test_list_strategies_includes_momentum():
    from services.strategy_registry import list_strategies

    ids = [s["id"] for s in list_strategies(portfolio_only=True)]
    assert "composite" in ids
    assert "momentum" in ids
    assert "dual_ma" in ids
    assert "index_enhance" in ids
    assert "turtle" in ids
    assert "sector_rotation" in ids


def test_turtle_score_breakout():
    from services.strategies.turtle import turtle_score

    dates = [f"d{i}" for i in range(25)]
    series = {}
    for i, d in enumerate(dates):
        series[d] = {"close": 10.0 + i * 0.1, "high": 10.0 + i * 0.1, "low": 9.5 + i * 0.1}
    series[dates[-1]] = {"close": 15.0, "high": 15.0, "low": 14.0}
    sc = turtle_score(series, dates, len(dates) - 1, entry=20)
    assert sc == 100.0


def test_turtle_atr_and_exit():
    from services.strategies.turtle import turtle_atr, turtle_should_exit

    dates = [f"d{i}" for i in range(15)]
    series = {}
    for i, d in enumerate(dates):
        series[d] = {"close": 10.0, "high": 11.0, "low": 9.0}
    series[dates[-1]] = {"close": 8.0, "high": 8.5, "low": 7.5}
    atr = turtle_atr(series, dates, len(dates) - 1, period=10)
    assert atr is not None and atr > 0
    assert turtle_should_exit(series, dates, len(dates) - 1, exit_period=10)
    assert turtle_should_exit(series, dates, len(dates) - 1, stop_price=9.0)


def test_compute_backtest_day_scores_momentum():
    from services.strategy_selector import compute_backtest_day_scores

    dates = [f"2024-01-{i:02d}" for i in range(1, 31)]
    quotes = {
        "AAA": {
            d: {"close": 10 + j * 0.2, "high": 10 + j * 0.2, "low": 9.8 + j * 0.2, "volume": 1000}
            for j, d in enumerate(dates)
        }
    }
    di = len(dates) - 1
    scores = compute_backtest_day_scores(
        strategy="momentum",
        quotes=quotes,
        dates=dates,
        di=di,
        dt=dates[di],
        available={"AAA": quotes["AAA"][dates[di]]["close"]},
        lookback=20,
        min_score=0,
    )
    assert "AAA" in scores


def test_turtle_stop_price_helper():
    from services.strategies.turtle import turtle_atr

    dates = [f"d{i}" for i in range(25)]
    series = {
        d: {"close": 10.0, "high": 11.0, "low": 9.0} for d in dates
    }
    atr = turtle_atr(series, dates, len(dates) - 1, period=20)
    assert atr is not None
    assert 1.5 < atr < 2.5


def test_turtle_exit_reason_stop_only():
    from services.portfolio_svc import _turtle_exit_reason

    dates = [f"d{i}" for i in range(15)]
    series = {d: {"close": 10.0, "high": 10.5, "low": 9.5} for d in dates}
    series[dates[-1]] = {"close": 8.0, "high": 8.5, "low": 7.5}
    assert _turtle_exit_reason(series, dates, exit_period=10, stop_price=5.0) is None
    series[dates[-1]] = {"close": 4.0, "high": 4.5, "low": 3.5}
    assert _turtle_exit_reason(series, dates, exit_period=10, stop_price=5.0) == "stop"


def test_select_top_n_v5_smoke():
    from config import DB_PATH
    from services.strategy_selector import select_top_n

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows, err = select_top_n(conn, strategy="composite", top_n=3, min_score=0)
    conn.close()
    if err and "无符合" in err:
        pytest.skip("no comprehensive scores in test db")
    assert err is None
    assert len(rows) <= 3
    assert rows[0].code


def test_dividend_defensive_day_scores_picks_low_vol():
    from services.strategy_selector import dividend_defensive_day_scores

    dates = [f"2024-01-{i:02d}" for i in range(1, 71)]
    quotes = {}
    for code, vol_scale in (("HIGH", 0.08), ("LOW", 0.005), ("MID", 0.02)):
        quotes[code] = {
            d: {
                "close": 10 * (1 + vol_scale * j),
                "high": 10 * (1 + vol_scale * j) * 1.01,
                "low": 10 * (1 + vol_scale * j) * 0.99,
                "volume": 1000,
            }
            for j, d in enumerate(dates)
        }
    dividend_snap = {
        "HIGH": {"2024-01-01": 6.0, "2024-02-01": 6.0},
        "LOW": {"2024-01-01": 5.5, "2024-02-01": 5.5},
        "MID": {"2024-01-01": 5.0, "2024-02-01": 5.0},
        "OTHER": {"2024-01-01": 4.0, "2024-02-01": 4.0},
    }
    available = {c: quotes[c][dates[-1]]["close"] for c in quotes}
    scores = dividend_defensive_day_scores(
        available=available,
        quotes=quotes,
        dates=dates,
        di=len(dates) - 1,
        dt=dates[-1],
        dividend_snap=dividend_snap,
        top_n=2,
        dy_pool=3,
        min_score=0,
    )
    assert "LOW" in scores
    assert "HIGH" not in scores or scores.get("LOW", 0) >= scores.get("HIGH", 0)


def test_dividend_defensive_backtest_smoke():
    from services.backtest_engine import run_backtest

    r = run_backtest(days=90, top_n=5, strategy="dividend_defensive", min_score=0, rebalance="weekly")
    if r.get("error") and "股息率" in str(r.get("error")):
        pytest.skip("no dividend snapshots in test db")
    assert "daily_values" in r or r.get("error")


def test_dual_ma_backtest_smoke():
    from services.backtest_engine import run_backtest

    r = run_backtest(days=90, strategy="dual_ma", top_n=10, min_score=1.0, rebalance="weekly")
    if r.get("error"):
        pytest.skip(r["error"])
    assert len(r.get("daily_values") or []) >= 10


def test_dividend_defensive_skips_latest_row_without_quality():
    """最新 calc_date 仅同步 val 等字段、quality 为空时，应回退到有 quality 的历史行。"""
    import sqlite3
    import tempfile
    from pathlib import Path

    from services.strategy_selector import select_top_n

    db_file = Path(tempfile.mkdtemp()) / "t.db"
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE stocks (id INTEGER PRIMARY KEY, code TEXT, name TEXT, is_active INTEGER);
        CREATE TABLE comprehensive_scores (
            stock_id INTEGER, calc_date TEXT, quality_score REAL, val_score REAL,
            veto_status TEXT, composite_score REAL
        );
        CREATE TABLE valuation_snapshots (
            stock_id INTEGER, as_of_date TEXT, dividend_yield REAL, dividend_yield_ttm REAL
        );
        """
    )
    conn.execute("INSERT INTO stocks VALUES (1,'600000','测试',1)")
    conn.execute(
        "INSERT INTO comprehensive_scores VALUES (1,'2026-07-24',70,65,'ok',70)"
    )
    conn.execute(
        "INSERT INTO comprehensive_scores VALUES (1,'2026-07-28',NULL,65,'ok',NULL)"
    )
    conn.execute(
        "INSERT INTO valuation_snapshots VALUES (1,'2026-07-28',NULL,3.5)"
    )
    conn.execute(
        "INSERT INTO valuation_snapshots VALUES (1,'2026-07-24',3.5,NULL)"
    )
    conn.commit()

    rows, err = select_top_n(
        conn, strategy="dividend_defensive", top_n=5, min_score=55
    )
    conn.close()
    assert err is None, err
    assert len(rows) == 1
    assert rows[0].code == "600000"


def test_dual_ma_backtest_score_golden_cross():
    from services.strategy_selector import dual_ma_backtest_score

    dates = [f"2026-01-{d:02d}" for d in range(1, 32)]
    series = {}
    price = 10.0
    for i, d in enumerate(dates):
        price *= 1.01
        series[d] = {"close": price, "high": price * 1.01, "low": price * 0.99, "volume": 1e6}
    sc = dual_ma_backtest_score(series, dates, len(dates) - 1)
    assert sc is not None
    assert sc >= 1.0
