"""A 股模拟交易规则 — 回测与模拟盘共用"""
from __future__ import annotations

import os

from services.v5_score_query import (
    SCORE_SPECS,
    STRATEGY_ALIASES,
    resolve_score_spec,
    resolve_strategy,
)

STAMP_TAX = 0.0005
COMMISSION = 0.0003
BACKTEST_COMMISSION = float(os.getenv("AFR_BACKTEST_COMMISSION", "0.00025"))
SLIPPAGE = float(os.getenv("AFR_BACKTEST_SLIPPAGE", "0.001"))
LIMIT_UP_PCT = float(os.getenv("AFR_LIMIT_UP_PCT", "9.8"))
LIMIT_DOWN_PCT = float(os.getenv("AFR_LIMIT_DOWN_PCT", "-9.8"))
LOT_SIZE = 100

# 兼容旧 import：composite → composite_v5
SCORE_STRATEGIES: dict[str, str] = {
    k: (v.source if v.kind == "column" else f"v5_dim:{v.source}")
    for k, v in SCORE_SPECS.items()
}


def normalize_shares(shares: int) -> int:
    return max(0, int(shares / LOT_SIZE) * LOT_SIZE)


def validate_shares(shares: int) -> str | None:
    if shares <= 0:
        return "数量须大于 0"
    if shares % LOT_SIZE != 0:
        return f"数量须为 {LOT_SIZE} 的整数倍"
    return None


def split_cost(amount: float, action: str) -> tuple[float, float, float]:
    """返回 (cash_delta, commission, tax) — buy 时 cash_delta 为负"""
    commission = round(amount * COMMISSION, 2)
    if action == "buy":
        cash_delta = -(amount + commission)
        return cash_delta, commission, 0.0
    tax = round(amount * STAMP_TAX, 2)
    cash_delta = amount - commission - tax
    return cash_delta, commission, tax


def cost_basis_price(price: float) -> float:
    """含买入佣金的成本价（每股）"""
    return round(price * (1 + COMMISSION), 4)


def apply_slippage(price: float, action: str) -> float:
    if action == "buy":
        return price * (1 + SLIPPAGE)
    return price * (1 - SLIPPAGE)


def trade_allowed(
    action: str,
    quote: dict,
    prev_close: float | None = None,
) -> bool:
    """涨跌停 / 停牌过滤。停牌日不允许买卖（持仓不动）。

    change_pct 缺失时尝试用 prev_close 补算；仍无法确定时保守拒绝并记录 warning。
    """
    import logging

    vol = float(quote.get("volume") or 0)
    if vol <= 0 or quote.get("is_suspended"):
        return False

    chg = quote.get("change_pct")
    if chg is None and prev_close and prev_close > 0:
        close = float(quote.get("close") or 0)
        if close > 0:
            chg = (close - prev_close) / prev_close * 100
    if chg is None:
        logging.getLogger(__name__).warning(
            "trade_allowed: change_pct 缺失且无法补算（action=%s），保守拒绝", action
        )
        return False

    chg = float(chg)
    if action == "buy" and chg >= LIMIT_UP_PCT:
        return False
    if action == "sell" and chg <= LIMIT_DOWN_PCT:
        return False
    return True
