"""test_backtest_execution.py — BT-SIM P0 验收单测
覆盖：
  H1 max_drawdown_period 起点 = 峰值日（非 dates[0]）
  H2 exec_model / effective_trading_days 字段存在
  H3 trade_allowed change_pct 缺失时 prev_close 补算 + 保守拒绝
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import math
import pytest
from services.trading_rules import trade_allowed


# ─── H1：最大回撤起始日 ────────────────────────────────────────────────────────

def _calc_drawdown_period(daily_records):
    """从 daily_records 列表复现 backtest_engine 的 max_dd 计算。"""
    peak = daily_records[0]["value"]
    peak_date = daily_records[0]["date"]
    max_dd, max_dd_s, max_dd_e = 0, "", ""
    for r in daily_records:
        v = r["value"]
        if v > peak:
            peak = v
            peak_date = r["date"]
        dd = (peak - v) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd, max_dd_s, max_dd_e = dd, peak_date, r["date"]
    return max_dd_s, max_dd_e


def test_max_drawdown_period_starts_at_peak_not_day0():
    """先涨后跌：峰值在 2024-01-03，回撤起点必须是 2024-01-03，不是 2024-01-01。"""
    records = [
        {"date": "2024-01-01", "value": 100_000},
        {"date": "2024-01-02", "value": 105_000},
        {"date": "2024-01-03", "value": 110_000},  # ← 峰值
        {"date": "2024-01-04", "value": 100_000},
        {"date": "2024-01-05", "value":  95_000},  # ← 最大回撤终点
    ]
    s, e = _calc_drawdown_period(records)
    assert s == "2024-01-03", f"回撤起点应为峰值日 2024-01-03，实际: {s}"
    assert e == "2024-01-05", f"回撤终点应为 2024-01-05，实际: {e}"


def test_max_drawdown_period_monotone_rise_no_drawdown():
    """单调上涨时 max_dd=0，日期字段为空串。"""
    records = [{"date": f"2024-01-0{i}", "value": 100_000 + i * 1000} for i in range(1, 6)]
    s, e = _calc_drawdown_period(records)
    # 无回撤，日期应是初始空串
    assert s == "" and e == ""


def test_max_drawdown_multiple_peaks():
    """两个峰值，最大回撤应从第二个峰开始。"""
    records = [
        {"date": "2024-01-01", "value": 100_000},
        {"date": "2024-01-02", "value": 108_000},  # 峰1
        {"date": "2024-01-03", "value": 102_000},
        {"date": "2024-01-04", "value": 112_000},  # 峰2
        {"date": "2024-01-05", "value":  88_000},  # 最大回撤（-21%）
    ]
    s, e = _calc_drawdown_period(records)
    assert s == "2024-01-04", f"最大回撤应从峰2（2024-01-04）开始，实际: {s}"
    assert e == "2024-01-05"


# ─── H2：exec_model / effective_trading_days ──────────────────────────────────

def test_run_backtest_returns_exec_model_field():
    """run_backtest 返回结果 params 内须含 exec_model 和 effective_trading_days。"""
    from services.backtest_engine import run_backtest
    result = run_backtest(days=30, top_n=3, strategy="composite", exec_mode="next_open")
    if "error" in result:
        pytest.skip(f"回测数据不足，跳过: {result['error']}")
    params = result.get("params", {})
    assert "exec_model" in params, "params 缺少 exec_model 字段"
    assert params["exec_model"] == "next_open"
    assert "effective_trading_days" in params, "params 缺少 effective_trading_days"
    assert "warmup_days" in params
    assert params["warmup_days"] == 1


def test_run_backtest_close_mode_no_warmup():
    """close 模式 warmup_days 应为 0。"""
    from services.backtest_engine import run_backtest
    result = run_backtest(days=30, top_n=3, strategy="composite", exec_mode="close")
    if "error" in result:
        pytest.skip(f"回测数据不足，跳过: {result['error']}")
    params = result.get("params", {})
    assert params.get("exec_model") == "close"
    assert params.get("warmup_days") == 0


def test_next_open_differs_from_close():
    """next_open 与 close 模式总收益应存在可观察差异（|diff| > 0）。"""
    from services.backtest_engine import run_backtest
    r_open = run_backtest(days=60, top_n=3, strategy="composite", exec_mode="next_open")
    r_close = run_backtest(days=60, top_n=3, strategy="composite", exec_mode="close")
    if "error" in r_open or "error" in r_close:
        pytest.skip("回测数据不足")
    diff = abs(r_open["total_return_pct"] - r_close["total_return_pct"])
    assert diff > 0, "next_open 与 close 模式总收益完全相同，可能 open 字段未生效"


# ─── H3：trade_allowed change_pct 补算 ────────────────────────────────────────

def test_trade_allowed_normal_buy():
    quote = {"volume": 1_000_000, "change_pct": 5.0}
    assert trade_allowed("buy", quote) is True


def test_trade_allowed_limit_up_blocks_buy():
    quote = {"volume": 1_000_000, "change_pct": 10.0}
    assert trade_allowed("buy", quote) is False


def test_trade_allowed_limit_down_blocks_sell():
    quote = {"volume": 1_000_000, "change_pct": -10.0}
    assert trade_allowed("sell", quote) is False


def test_trade_allowed_infers_change_pct_from_prev_close():
    """change_pct 缺失，通过 prev_close 补算出涨停（+10%），应拦截 buy。"""
    quote = {"volume": 1_000_000, "close": 11.0}  # 无 change_pct
    prev_close = 10.0  # → chg = +10%
    assert trade_allowed("buy", quote, prev_close=prev_close) is False


def test_trade_allowed_infers_normal_via_prev_close():
    """补算后涨幅正常（+5%），buy 应通过。"""
    quote = {"volume": 1_000_000, "close": 10.5}
    prev_close = 10.0  # → chg = +5%
    assert trade_allowed("buy", quote, prev_close=prev_close) is True


def test_trade_allowed_no_change_pct_no_prev_close_rejects():
    """change_pct 缺失且无 prev_close → 保守拒绝。"""
    quote = {"volume": 1_000_000, "close": 10.0}
    assert trade_allowed("buy", quote) is False


def test_trade_allowed_suspended_blocks():
    quote = {"volume": 0, "change_pct": 0}
    assert trade_allowed("buy", quote) is False
    quote2 = {"volume": 1_000_000, "is_suspended": 1, "change_pct": 0}
    assert trade_allowed("sell", quote2) is False
