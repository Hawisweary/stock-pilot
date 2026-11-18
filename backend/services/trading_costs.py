"""统一成本模型与交易规则(重构清单 P11)。

唯一配置来源:backend/trading_costs.yaml(或环境变量 AFR_TRADING_COSTS_FILE 指定的文件)。
自研回测、模拟盘、以后的 qlib Exchange 都从这里取费率和涨跌停规则。

- CostModel:佣金(含最低佣金)、印花税(仅卖出)、过户费(双向)、滑点
- board_of / limit_pct / limit_prices / hits_limit:分板块涨跌停
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from datetime import date
from functools import lru_cache

import yaml

_DEFAULT_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "trading_costs.yaml")

# 配置文件缺失时的兜底值(与 trading_costs.yaml 保持一致)
_FALLBACK = {
    "cn_a": {
        "commission_rate": 0.0003, "min_commission": 5.0, "stamp_tax_rate": 0.0005,
        "transfer_fee_rate": 0.00001, "slippage": 0.001, "lot_size": 100, "t_plus": 1,
        "price_limits": {"main": 10, "st": 5, "chinext": 20, "chinext_20_since": "2020-08-24",
                         "star": 20, "bse": 30, "no_limit_first_days": 5},
    },
    "us": {
        "commission_rate": 0.0, "min_commission": 0.0, "stamp_tax_rate": 0.0, "transfer_fee_rate": 0.0,
        "slippage": 0.001, "lot_size": 1, "t_plus": 0, "price_limits": None,
    },
}


@lru_cache(maxsize=4)
def load_config(path: str | None = None) -> dict:
    path = path or os.getenv("AFR_TRADING_COSTS_FILE") or _DEFAULT_FILE
    if not os.path.exists(path):
        return _FALLBACK
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    for market, defaults in _FALLBACK.items():
        merged = dict(defaults)
        merged.update(data.get(market) or {})
        data[market] = merged
    return data


@dataclass(frozen=True)
class CostModel:
    commission_rate: float
    min_commission: float
    stamp_tax_rate: float
    transfer_fee_rate: float
    slippage: float
    lot_size: int = 100

    # ── 费用 ──
    def commission(self, amount: float) -> float:
        if amount <= 0 or self.commission_rate <= 0 and self.min_commission <= 0:
            return 0.0
        return max(amount * self.commission_rate, self.min_commission)

    def buy_fees(self, amount: float) -> float:
        """买入总费用(佣金 + 过户费)。"""
        if amount <= 0:
            return 0.0
        return self.commission(amount) + amount * self.transfer_fee_rate

    def sell_fees(self, amount: float) -> float:
        """卖出总费用(佣金 + 印花税 + 过户费)。"""
        if amount <= 0:
            return 0.0
        return self.commission(amount) + amount * (self.stamp_tax_rate + self.transfer_fee_rate)

    def split(self, amount: float, action: str) -> tuple[float, float, float]:
        """(现金变动, 佣金及过户费, 印花税);买入时现金变动为负。保留两位小数。"""
        if action == "buy":
            fee = round(self.buy_fees(amount), 2)
            return -(amount + fee), fee, 0.0
        tax = round(amount * self.stamp_tax_rate, 2)
        fee = round(self.sell_fees(amount) - amount * self.stamp_tax_rate, 2)
        return amount - fee - tax, fee, tax

    # ── 成交价 ──
    def buy_price(self, price: float) -> float:
        return price * (1 + self.slippage)

    def sell_price(self, price: float) -> float:
        return price * (1 - self.slippage)

    # ── 下单数量 ──
    def max_affordable_shares(self, cash: float, price: float) -> int:
        """在 cash 内(含费用)能买的最大整手股数。"""
        if cash <= 0 or price <= 0:
            return 0
        lot = max(1, int(self.lot_size))
        shares = int(cash / (price * (1 + self.commission_rate + self.transfer_fee_rate)) / lot) * lot
        while shares > 0 and shares * price + self.buy_fees(shares * price) > cash + 1e-9:
            shares -= lot
        return max(0, shares)

    def without_costs(self) -> "CostModel":
        return replace(self, commission_rate=0.0, min_commission=0.0, stamp_tax_rate=0.0, transfer_fee_rate=0.0)


def cost_model(market: str = "cn_a", **overrides) -> CostModel:
    """按市场取成本模型;overrides 中值为 None 的项忽略(便于直接透传可选参数)。"""
    c = load_config()[market]
    m = CostModel(
        commission_rate=float(c["commission_rate"]),
        min_commission=float(c["min_commission"]),
        stamp_tax_rate=float(c["stamp_tax_rate"]),
        transfer_fee_rate=float(c["transfer_fee_rate"]),
        slippage=float(c["slippage"]),
        lot_size=int(c["lot_size"]),
    )
    kw = {k: v for k, v in overrides.items() if v is not None}
    return replace(m, **kw) if kw else m


# ── 分板块涨跌停 ──────────────────────────────────────────
def board_of(code: str) -> str:
    """main / chinext / star / bse;无法识别的按主板处理。"""
    c = str(code or "").strip().upper()
    for p in ("SH", "SZ", "BJ"):
        if c.startswith(p):
            c = c[2:]
    c = c.split(".")[0]
    if c.startswith(("688", "689")):
        return "star"
    if c.startswith(("300", "301")):
        return "chinext"
    if c.startswith(("43", "83", "87", "88", "92")) or (len(c) == 6 and c[0] in "48"):
        return "bse"
    return "main"


def _as_date(d) -> date | None:
    if d is None:
        return None
    if isinstance(d, date):
        return d
    s = str(d)
    try:
        return date.fromisoformat(s[:10]) if "-" in s else date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except (ValueError, IndexError):
        return None


def limit_pct(code: str, trade_date=None, is_st: bool = False, listed_days: int | None = None) -> float | None:
    """涨跌停幅度(%);None 表示不设涨跌幅(注册制新股前几日)。"""
    lim = load_config()["cn_a"]["price_limits"] or {}
    board = board_of(code)
    if listed_days is not None and listed_days < int(lim.get("no_limit_first_days", 0)) and board in ("chinext", "star", "bse"):
        return None
    if board == "star":
        return float(lim.get("star", 20))
    if board == "bse":
        return float(lim.get("bse", 30))
    if board == "chinext":
        since = _as_date(lim.get("chinext_20_since"))
        d = _as_date(trade_date)
        if since and d and d < since:
            return float(lim.get("st", 5)) if is_st else float(lim.get("main", 10))
        return float(lim.get("chinext", 20))
    return float(lim.get("st", 5)) if is_st else float(lim.get("main", 10))


def limit_prices(prev_close: float, code: str, trade_date=None, is_st: bool = False,
                 listed_days: int | None = None) -> tuple[float | None, float | None]:
    """(涨停价, 跌停价),按交易所规则四舍五入到分。"""
    pct = limit_pct(code, trade_date, is_st, listed_days)
    if pct is None or not prev_close or prev_close <= 0:
        return None, None
    up = round(prev_close * (1 + pct / 100) + 1e-9, 2)
    down = round(prev_close * (1 - pct / 100) + 1e-9, 2)
    return up, down


def hits_limit(action: str, code: str | None, change_pct: float | None = None, close: float | None = None,
               prev_close: float | None = None, trade_date=None, is_st: bool = False) -> bool:
    """买入撞涨停 / 卖出撞跌停(按涨跌幅判断)。

    优先用数据里的涨跌幅(交易所按原始价计算);缺失时用 收盘 / 昨收 补算。
    容差为幅度的 2%(主板 10% → 9.8%,与原先口径一致)。code 为空时按主板处理。
    回测的价格可能是复权价,所以这里不用"涨跌停价"做精确比较;
    模拟盘有原始价时用 limit_prices() 得到涨跌停价再比较。
    """
    pct = limit_pct(code or "", trade_date, is_st)
    if pct is None:
        return False
    if change_pct is None and close and prev_close and prev_close > 0:
        change_pct = (close - prev_close) / prev_close * 100
    if change_pct is None:
        return False
    tol = pct * 0.02
    if action == "buy":
        return change_pct >= pct - tol
    return change_pct <= -(pct - tol)
