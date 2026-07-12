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


def test_turtle_exit_reason_channel():
    from services.portfolio_svc import _turtle_exit_reason

    dates = [f"d{i}" for i in range(15)]
    series = {d: {"close": 10.0, "high": 10.5, "low": 9.5} for d in dates}
    series[dates[-1]] = {"close": 8.0, "high": 8.5, "low": 7.5}
    reason = _turtle_exit_reason(series, dates, exit_period=10, stop_price=5.0)
    assert reason == "channel"


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
