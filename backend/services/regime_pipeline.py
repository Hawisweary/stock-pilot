"""L1→L2→L3 市场状态流水线 — 调度与脚本统一入口。"""
from __future__ import annotations

import sqlite3
from typing import Any, Optional

import config
from services.market_regime import sync_regime
from services.strategy_recommender import generate_and_persist_recommendation
from services.strategy_regime_performance import refresh_strategy_regime_matrix


def run_regime_l2_l3_pipeline(
    conn: sqlite3.Connection,
    *,
    skip_regime: bool = False,
    refresh_matrix: bool = False,
    lookback_days: Optional[int] = None,
    backtest_days: Optional[int] = None,
) -> dict[str, Any]:
    """sync_regime → 可选 L2 矩阵 → L3 推荐 + 监控 outcomes。"""
    steps: dict[str, Any] = {}

    if not skip_regime:
        steps["regime"] = sync_regime(conn, apply_persistence=True)

    if refresh_matrix:
        steps["l2_matrix"] = refresh_strategy_regime_matrix(
            conn,
            lookback_days=lookback_days or config.REGIME_MATRIX_LOOKBACK_DAYS,
            backtest_days=backtest_days or config.REGIME_MATRIX_BACKTEST_DAYS,
        )

    steps["l3_recommendation"] = generate_and_persist_recommendation(conn)

    ok = all(
        "error" not in (v or {})
        for k, v in steps.items()
        if k != "l3_recommendation" or not isinstance(v, dict) or v.get("recommendation")
    )
    rec = (steps.get("l3_recommendation") or {}).get("recommendation") or {}
    primary = rec.get("primary") or {}

    return {
        "ok": ok and bool(primary.get("strategy")),
        "steps": steps,
        "trade_date": (steps.get("l3_recommendation") or {}).get("trade_date"),
        "regime_bucket": ((steps.get("l3_recommendation") or {}).get("market") or {}).get("regime_bucket"),
        "primary_strategy": primary.get("strategy"),
        "matrix_refreshed": refresh_matrix,
    }
