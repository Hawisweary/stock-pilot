"""P11 统一成本模型与分板块涨跌停(不依赖数据库、不联网)。"""
import pytest

from services.trading_costs import (
    board_of,
    cost_model,
    hits_limit,
    limit_pct,
    limit_prices,
    load_config,
)
from services.trading_rules import (
    BACKTEST_COMMISSION,
    COMMISSION,
    SLIPPAGE,
    STAMP_TAX,
    cost_basis_price,
    max_affordable_shares,
    split_cost,
    trade_allowed,
)
from services.trade_pricing import SLIPPAGE_PCT


# ── 单一来源:回测、模拟盘、配置文件三处一致 ──
def test_backtest_and_paper_use_same_rates():
    cfg = load_config()["cn_a"]
    assert BACKTEST_COMMISSION == COMMISSION == cfg["commission_rate"]
    assert STAMP_TAX == cfg["stamp_tax_rate"]
    assert SLIPPAGE == SLIPPAGE_PCT == cfg["slippage"]


# ── 费用 ──
def test_min_commission_applies_to_small_trades():
    cm = cost_model()
    # 1 万元 × 万 3 = 3 元 < 5 元 → 按 5 元收
    assert cm.commission(10_000) == pytest.approx(5.0)
    # 10 万元 × 万 3 = 30 元
    assert cm.commission(100_000) == pytest.approx(30.0)


def test_buy_fees_have_no_stamp_tax_sell_fees_do():
    cm = cost_model()
    amt = 100_000
    assert cm.buy_fees(amt) == pytest.approx(30 + amt * cm.transfer_fee_rate)
    assert cm.sell_fees(amt) == pytest.approx(30 + amt * (cm.stamp_tax_rate + cm.transfer_fee_rate))


def test_split_cost_matches_cost_model():
    cash, fee, tax = split_cost(100_000, "buy")
    assert tax == 0.0
    assert cash == pytest.approx(-(100_000 + fee))
    cash, fee, tax = split_cost(100_000, "sell")
    assert tax == pytest.approx(50.0)
    assert cash == pytest.approx(100_000 - fee - tax)


def test_without_costs_is_free():
    cm = cost_model().without_costs()
    assert cm.buy_fees(50_000) == 0 and cm.sell_fees(50_000) == 0


def test_overrides_ignore_none():
    cm = cost_model(commission_rate=None, slippage=0.002)
    assert cm.commission_rate == COMMISSION and cm.slippage == 0.002


def test_max_affordable_shares_respects_min_commission():
    # 现金 1005 元、价格 10 元:按万 3 估算能买 100 股,但 1000 + 最低 5 元佣金 + 过户费 > 1005
    assert max_affordable_shares(1005, 10.0) == 0
    assert max_affordable_shares(1006, 10.0) == 100
    sh = max_affordable_shares(100_000, 37.5)
    cm = cost_model()
    assert sh % 100 == 0 and sh * 37.5 + cm.buy_fees(sh * 37.5) <= 100_000


def test_cost_basis_with_shares_includes_min_commission():
    # 100 股 × 10 元 = 1000 元,费用 = 5 元最低佣金 + 过户费 0.01 元
    assert cost_basis_price(10.0, 100) == pytest.approx((1000 + 5.01) / 100, abs=1e-4)


# ── 分板块涨跌停 ──
@pytest.mark.parametrize("code,board", [
    ("600519", "main"), ("000001", "main"), ("002594", "main"), ("SH600036", "main"),
    ("300750", "chinext"), ("301091", "chinext"), ("688981", "star"), ("689009", "star"),
    ("430047", "bse"), ("830799", "bse"), ("920099", "bse"),
])
def test_board_of(code, board):
    assert board_of(code) == board


def test_limit_pct_by_board_and_date():
    assert limit_pct("600519") == 10
    assert limit_pct("600519", is_st=True) == 5
    assert limit_pct("688981") == 20
    assert limit_pct("830799") == 30
    assert limit_pct("300750", "2026-10-26") == 20
    assert limit_pct("300750", "2020-08-21") == 10  # 创业板注册制改革前
    assert limit_pct("300999", listed_days=2) is None  # 注册制新股前 5 日不设涨跌幅


def test_limit_prices_round_to_cent():
    assert limit_prices(10.0, "600519") == (11.0, 9.0)
    assert limit_prices(10.0, "300750") == (12.0, 8.0)
    assert limit_prices(10.0, "830799") == (13.0, 7.0)


def test_hits_limit_by_board():
    # 主板涨 9.9% 已涨停;创业板涨 9.9% 还能买
    assert hits_limit("buy", "600519", change_pct=9.9) is True
    assert hits_limit("buy", "300750", change_pct=9.9) is False
    assert hits_limit("buy", "300750", change_pct=19.9) is True
    assert hits_limit("sell", "688981", change_pct=-19.7) is True
    assert hits_limit("sell", "688981", change_pct=-15.0) is False
    # 没给代码按主板(与原先 ±9.8% 口径一致)
    assert hits_limit("buy", None, change_pct=9.8) is True
    assert hits_limit("buy", None, change_pct=9.7) is False


def test_trade_allowed_uses_board_when_code_given():
    q = {"volume": 1000, "change_pct": 12.0, "close": 11.2}
    assert trade_allowed("buy", q, code="300750", trade_date="2026-10-26") is True
    assert trade_allowed("buy", q, code="600519", trade_date="2026-10-26") is False
    assert trade_allowed("buy", q) is False  # 不传代码按主板
