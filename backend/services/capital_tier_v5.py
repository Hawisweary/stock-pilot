"""V5 资金面五档 — 主力流 + 融资 + 龙虎榜 + 北向持股（辅助）。"""
from __future__ import annotations

import sqlite3
from typing import Any

import config

CAPITAL_SUB_WEIGHTS: dict[str, float] = {
    "main_flow": 0.40,
    "margin": 0.30,
    "lhb": 0.20,
    "north": 0.10,
}


def _clamp_tier(t: float) -> int:
    return max(-2, min(2, round(t)))


def _tier_main_flow(main_net_5d: float | None) -> int | None:
    """主力净流 5 日档位。
    A-4：-2 阈值从 -5e7 放宽至 config.V5_CAPITAL_FLOW_MINUS2_THRESHOLD（默认 -1.5亿），
    避免震荡市中等市值股因少量净流出被误判为极差。
    """
    if main_net_5d is None:
        return None
    v = float(main_net_5d)
    minus2_thresh = config.V5_CAPITAL_FLOW_MINUS2_THRESHOLD  # 默认 -1.5e8
    if v > 5e7:
        return 2
    if v > 0:
        return 1
    if v < minus2_thresh:
        return -2
    if v < 0:
        return -1
    return 0


def _tier_margin_chg(chg_pct: float | None) -> int | None:
    if chg_pct is None:
        return None
    v = float(chg_pct)
    if v > 5:
        return 2
    if v > 1:
        return 1
    if v < -5:
        return -2
    if v < -1:
        return -1
    return 0


def _tier_lhb_net(net_wan: float | None) -> int | None:
    """net_wan：近 20 日龙虎榜净买合计（万元）。"""
    if net_wan is None:
        return None
    v = float(net_wan)
    if v > 5000:
        return 2
    if v > 0:
        return 1
    if v < -5000:
        return -2
    if v < 0:
        return -1
    return 0


def _tier_north_ratio_chg(ratio_chg_pp: float | None) -> int | None:
    """ratio_chg_pp：北向持股占比变化（百分点）。"""
    if ratio_chg_pp is None:
        return None
    v = float(ratio_chg_pp)
    if v > 1.0:
        return 2
    if v > 0.3:
        return 1
    if v < -1.0:
        return -2
    if v < -0.3:
        return -1
    return 0


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        is not None
    )


def _margin_chg_5d(conn: sqlite3.Connection, stock_id: int) -> float | None:
    if not _table_exists(conn, "eastmoney_margin"):
        return None
    rows = conn.execute(
        """SELECT margin_balance FROM eastmoney_margin
           WHERE stock_id=? AND margin_balance IS NOT NULL
           ORDER BY date DESC LIMIT 6""",
        (stock_id,),
    ).fetchall()
    if len(rows) < 6:
        return None
    cur, prev = float(rows[0][0]), float(rows[5][0])
    if prev <= 0:
        return None
    return round((cur / prev - 1) * 100, 4)


def _lhb_net_20d(conn: sqlite3.Connection, stock_id: int) -> float | None:
    if not _table_exists(conn, "lhb_daily"):
        return None
    latest = conn.execute(
        "SELECT MAX(trade_date) FROM lhb_daily WHERE stock_id=?",
        (stock_id,),
    ).fetchone()
    if not latest or not latest[0]:
        return None
    row = conn.execute(
        """SELECT SUM(net_buy) FROM lhb_daily
           WHERE stock_id=? AND trade_date >= date(?, '-20 days')""",
        (stock_id, latest[0]),
    ).fetchone()
    if not row or row[0] is None:
        return None
    return float(row[0])


def _north_ratio_chg(conn: sqlite3.Connection, code: str) -> float | None:
    if not _table_exists(conn, "eastmoney_holdings"):
        return None
    norm = (code or "").strip()
    if len(norm) > 6:
        norm = norm[-6:]
    rows = conn.execute(
        """SELECT date, ratio FROM eastmoney_holdings
           WHERE code=? AND ratio IS NOT NULL
           ORDER BY date DESC LIMIT 2""",
        (norm,),
    ).fetchall()
    if len(rows) < 2:
        return None
    r0, r1 = float(rows[0][1]), float(rows[1][1])
    return round((r0 - r1) * 100, 4)


def compute_capital_tier_v5(
    stock_id: int,
    conn: sqlite3.Connection,
    *,
    code: str | None = None,
) -> dict[str, Any]:
    """多源资金面五档；缺源子项自动剔除并归一化权重。"""
    main_net = None
    if _table_exists(conn, "stock_fund_flow_daily"):
        flow = conn.execute(
            """SELECT main_net_5d FROM stock_fund_flow_daily
               WHERE stock_id=? ORDER BY trade_date DESC LIMIT 1""",
            (stock_id,),
        ).fetchone()
        main_net = float(flow[0]) if flow and flow[0] is not None else None
    if not code:
        srow = conn.execute("SELECT code FROM stocks WHERE id=?", (stock_id,)).fetchone()
        code = str(srow[0]) if srow else ""

    margin_chg = _margin_chg_5d(conn, stock_id)
    lhb_net = _lhb_net_20d(conn, stock_id)
    north_chg = _north_ratio_chg(conn, code)

    sub_tiers: dict[str, int | None] = {
        "main_flow": _tier_main_flow(main_net),
        "margin": _tier_margin_chg(margin_chg),
        "lhb": _tier_lhb_net(lhb_net),
        "north": _tier_north_ratio_chg(north_chg),
    }
    sub_raw: dict[str, float | None] = {
        "main_net_5d": main_net,
        "margin_chg_5d_pct": margin_chg,
        "lhb_net_20d_wan": lhb_net,
        "north_ratio_chg_pp": north_chg,
    }

    active: dict[str, float] = {}
    for key, w in CAPITAL_SUB_WEIGHTS.items():
        if sub_tiers[key] is not None:
            active[key] = w
    if not active:
        return {
            "tier": None,
            "sub_tiers": sub_tiers,
            "sub_raw": sub_raw,
            "effective_weights": {},
            "source": "capital_v5_multisource",
        }

    w_sum = sum(active.values())
    combined = sum(sub_tiers[k] * active[k] for k in active) / w_sum
    effective = {k: round(active[k] / w_sum, 4) for k in active}

    raw_tier = _clamp_tier(combined)

    # A-4：单源 -2 不足以判最终 -2；需 ≥2 个有效子源同向 ≤-1 才允许 -2
    if raw_tier == -2:
        minus2_count = sum(1 for k in active if (sub_tiers[k] or 0) <= -2)
        minus1_or_worse = sum(1 for k in active if (sub_tiers[k] or 0) <= -1)
        if minus2_count < 1 or minus1_or_worse < 2:
            raw_tier = -1  # 降一档，避免单源误判

    return {
        "tier": raw_tier,
        "sub_tiers": sub_tiers,
        "sub_raw": sub_raw,
        "effective_weights": effective,
        "source": "capital_v5_multisource",
    }
