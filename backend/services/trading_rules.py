"""A 股模拟交易规则 — 回测与模拟盘共用

费率和涨跌停规则统一来自 services/trading_costs.py(配置文件 backend/trading_costs.yaml,P11)。
下面的常量是给旧代码用的别名,做估算(如"大约能买多少股")可以用;实际扣费请用
split_cost / CostModel,它们包含最低佣金和过户费。
"""
from __future__ import annotations

from services.trading_costs import cost_model, hits_limit
from services.v5_score_query import (
    SCORE_SPECS,
    STRATEGY_ALIASES,
    resolve_score_spec,
    resolve_strategy,
)

_CM = cost_model("cn_a")
STAMP_TAX = _CM.stamp_tax_rate
COMMISSION = _CM.commission_rate
BACKTEST_COMMISSION = COMMISSION  # 回测与模拟盘同一费率(原先回测万 2.5、模拟盘万 3,已统一)
SLIPPAGE = _CM.slippage
LOT_SIZE = _CM.lot_size

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
    """返回 (cash_delta, commission, tax) — buy 时 cash_delta 为负。
    commission 含佣金(有最低佣金)和过户费;tax 为印花税(仅卖出)。"""
    return _CM.split(amount, action)


def max_affordable_shares(cash: float, price: float) -> int:
    """现金内(含全部费用)能买的最大整手股数。"""
    return _CM.max_affordable_shares(cash, price)


def cost_basis_price(price: float, shares: int | None = None) -> float:
    """含买入费用的成本价(每股)。给了 shares 时按实际费用(含最低佣金)计算。"""
    if shares and shares > 0:
        amount = price * shares
        return round((amount + _CM.buy_fees(amount)) / shares, 4)
    return round(price * (1 + COMMISSION), 4)


def apply_slippage(price: float, action: str) -> float:
    if action == "buy":
        return _CM.buy_price(price)
    return _CM.sell_price(price)


def trade_allowed(
    action: str,
    quote: dict,
    prev_close: float | None = None,
    code: str | None = None,
    trade_date: str | None = None,
) -> bool:
    """涨跌停 / 停牌过滤。停牌日不允许买卖（持仓不动）。

    涨跌停按板块区分(主板 10%、创业板 / 科创板 20%、北交所 30%,见 trading_costs.yaml);
    不传 code 时按主板处理。历史 ST 状态暂未接入(待 P5 清洗流水线)。
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

    return not hits_limit(action, code or quote.get("code"), change_pct=float(chg), trade_date=trade_date)
