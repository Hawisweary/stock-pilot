"""持仓盈亏口径：裸成本 / 累计 / 今日 / 交易成本。"""
from __future__ import annotations

from services.trade_pricing import SLIPPAGE_PCT
from services.trading_rules import COMMISSION


def buy_friction_multiplier() -> float:
    """买入成交价相对裸价的倍数（滑点 × 佣金）。"""
    return (1.0 + SLIPPAGE_PCT) * (1.0 + COMMISSION)


def estimated_buy_friction_pct() -> float:
    """买入成本相对裸价的系统性上浮（%）。"""
    return round((buy_friction_multiplier() - 1.0) * 100, 2)


def raw_entry_from_avg_cost(avg_cost: float) -> float:
    """从含费成本反推买入裸价。"""
    if avg_cost <= 0:
        return 0.0
    return round(avg_cost / buy_friction_multiplier(), 4)


def pct_change(mark: float, basis: float) -> float:
    if basis <= 0 or mark <= 0:
        return 0.0
    return round((mark - basis) / basis * 100, 2)


def weighted_pct(positions: list[dict], pct_key: str, weight_key: str = "cost") -> float:
    total_w = sum(float(p.get(weight_key) or 0) for p in positions)
    if total_w <= 0:
        return 0.0
    acc = sum(float(p.get(weight_key) or 0) * float(p.get(pct_key) or 0) for p in positions)
    return round(acc / total_w, 2)


def aggregate_totals(positions: list[dict]) -> dict:
    total_cost = sum(float(p.get("cost") or 0) for p in positions)
    total_mv = sum(float(p.get("market_value") or 0) for p in positions)
    total_pnl = round(total_mv - total_cost, 2)
    total_pnl_pct = pct_change(total_mv, total_cost) if total_cost > 0 else 0.0
    market_pnl_pct = weighted_pct(positions, "market_pnl_pct")
    today_pnl_pct = weighted_pct(positions, "today_pnl_pct")
    return {
        "total_cost": round(total_cost, 2),
        "total_market_value": round(total_mv, 2),
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl_pct,
        "market_pnl_pct": market_pnl_pct,
        "today_pnl_pct": today_pnl_pct,
        "position_count": len(positions),
    }


def build_position_pnl(
    *,
    code: str,
    name: str,
    stock_id: int,
    shares: int,
    avg_cost: float,
    buy_date: str,
    price: float,
    prev_close: float | None,
    calendar_date: str,
) -> dict:
    shares = int(shares or 0)
    avg_cost = float(avg_cost or 0)
    price = float(price or 0)
    cost = shares * avg_cost
    market_value = round(shares * price, 2)
    pnl = round(market_value - cost, 2)
    pnl_pct = pct_change(price, avg_cost)

    raw_entry = raw_entry_from_avg_cost(avg_cost)
    market_pnl_pct = pct_change(price, raw_entry)

    if prev_close and prev_close > 0:
        today_pnl_pct = pct_change(price, prev_close)
    elif buy_date and calendar_date and buy_date[:10] == calendar_date[:10]:
        today_pnl_pct = pct_change(price, raw_entry)
    else:
        today_pnl_pct = 0.0

    return {
        "stock_id": stock_id,
        "code": code,
        "name": name,
        "shares": shares,
        "avg_cost": avg_cost,
        "raw_entry_price": raw_entry,
        "buy_date": buy_date or "",
        "price": price,
        "market_value": market_value,
        "cost": round(cost, 2),
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "market_pnl_pct": market_pnl_pct,
        "today_pnl_pct": today_pnl_pct,
        "bought_today": bool(buy_date and calendar_date and buy_date[:10] == calendar_date[:10]),
    }


def dedupe_positions(
    positions: list[dict],
    *,
    prev_closes: dict[int, float] | None = None,
    calendar_date: str = "",
) -> list[dict]:
    """按 stock_id 合并多策略重复持仓（股数加权成本）。"""
    prev_closes = prev_closes or {}
    buckets: dict[int, dict] = {}
    for p in positions:
        sid = int(p["stock_id"])
        shares = int(p.get("shares") or 0)
        if shares <= 0:
            continue
        if sid not in buckets:
            buckets[sid] = {
                "stock_id": sid,
                "code": p["code"],
                "name": p["name"],
                "shares": 0,
                "cost_sum": 0.0,
                "market_value": 0.0,
                "price": float(p.get("price") or 0),
                "portfolio_names": set(),
            }
        b = buckets[sid]
        b["shares"] += shares
        b["cost_sum"] += shares * float(p.get("avg_cost") or 0)
        b["market_value"] += float(p.get("market_value") or 0)
        pname = p.get("portfolio_name") or ""
        if pname:
            b["portfolio_names"].add(pname)

    out: list[dict] = []
    for b in buckets.values():
        shares = b["shares"]
        avg_cost = round(b["cost_sum"] / shares, 4) if shares else 0.0
        price = b["price"]
        raw_entry = raw_entry_from_avg_cost(avg_cost)
        cost = round(shares * avg_cost, 2)
        market_value = round(b["market_value"], 2)
        prev_close = prev_closes.get(b["stock_id"])
        if prev_close and prev_close > 0:
            today_pnl_pct = pct_change(price, prev_close)
        else:
            today_pnl_pct = 0.0
        out.append({
            "stock_id": b["stock_id"],
            "code": b["code"],
            "name": b["name"],
            "shares": shares,
            "avg_cost": avg_cost,
            "raw_entry_price": raw_entry,
            "price": price,
            "market_value": market_value,
            "cost": cost,
            "pnl": round(market_value - cost, 2),
            "pnl_pct": pct_change(price, avg_cost),
            "market_pnl_pct": pct_change(price, raw_entry),
            "today_pnl_pct": today_pnl_pct,
            "strategy_count": len(b["portfolio_names"]),
        })
    return out
