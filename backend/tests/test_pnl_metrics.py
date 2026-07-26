from services.pnl_metrics import (
    aggregate_totals,
    build_position_pnl,
    dedupe_positions,
    estimated_buy_friction_pct,
    raw_entry_from_avg_cost,
)


def test_friction_pct_near_013():
    pct = estimated_buy_friction_pct()
    assert 0.12 <= pct <= 0.14


def test_raw_entry_reverses_cost_basis():
    avg = 57.074
    raw = raw_entry_from_avg_cost(avg)
    assert abs(raw - 57.0) < 0.01


def test_build_position_pnl_bought_today():
    pos = build_position_pnl(
        code="600519",
        name="茅台",
        stock_id=1,
        shares=100,
        avg_cost=57.074,
        buy_date="2026-07-24",
        price=57.0,
        prev_close=56.5,
        calendar_date="2026-07-24",
    )
    assert pos["pnl_pct"] == -0.13
    assert pos["market_pnl_pct"] == 0.0
    assert pos["bought_today"] is True


def test_dedupe_merges_same_stock():
    positions = [
        {
            "stock_id": 1, "code": "600036", "name": "招行",
            "shares": 200, "avg_cost": 36.0, "price": 39.0,
            "market_value": 7200, "portfolio_name": "A",
        },
        {
            "stock_id": 1, "code": "600036", "name": "招行",
            "shares": 300, "avg_cost": 39.0, "price": 39.0,
            "market_value": 11700, "portfolio_name": "B",
        },
    ]
    merged = dedupe_positions(positions, prev_closes={1: 38.0})
    assert len(merged) == 1
    assert merged[0]["shares"] == 500
    assert merged[0]["strategy_count"] == 2


def test_aggregate_totals_weighted():
    positions = [
        {"cost": 100, "market_value": 99, "market_pnl_pct": 0, "today_pnl_pct": 1.0},
        {"cost": 100, "market_value": 98, "market_pnl_pct": -2.0, "today_pnl_pct": -1.0},
    ]
    t = aggregate_totals(positions)
    assert t["total_pnl_pct"] == -1.5
    assert t["today_pnl_pct"] == 0.0
