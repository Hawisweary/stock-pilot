#!/usr/bin/env python3
"""一键：L1 regime + L2 矩阵 + L3 推荐（调度/运维统一入口）。"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from migrations import run_migrations
from services.regime_pipeline import run_regime_l2_l3_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="L1→L2→L3 市场状态流水线")
    parser.add_argument("--skip-regime", action="store_true", help="跳过 L1 sync_regime")
    parser.add_argument("--skip-matrix", action="store_true", help="跳过 L2 矩阵刷新")
    parser.add_argument("--lookback-days", type=int, default=config.REGIME_MATRIX_LOOKBACK_DAYS)
    parser.add_argument("--backtest-days", type=int, default=config.REGIME_MATRIX_BACKTEST_DAYS)
    args = parser.parse_args()

    conn = sqlite3.connect(config.DB_PATH, timeout=300)
    run_migrations(conn)

    result = run_regime_l2_l3_pipeline(
        conn,
        skip_regime=args.skip_regime,
        refresh_matrix=not args.skip_matrix,
        lookback_days=args.lookback_days,
        backtest_days=args.backtest_days,
    )

    l2 = (result.get("steps") or {}).get("l2_matrix") or {}
    if l2:
        td = conn.execute(
            """SELECT strategy_id, sample_days, sharpe FROM strategy_regime_metrics
               WHERE regime_bucket='trend_down' AND as_of_date=?
               ORDER BY sample_days DESC LIMIT 6""",
            (l2.get("as_of_date"),),
        ).fetchall()
        result["trend_down_top"] = td

    print(json.dumps(result, ensure_ascii=False, indent=2))
    conn.close()


if __name__ == "__main__":
    main()
