#!/usr/bin/env python3
"""刷新 L2 策略×状态绩效矩阵。"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from migrations import run_migrations
from services.strategy_regime_performance import get_strategy_regime_matrix, refresh_strategy_regime_matrix


def main() -> None:
    parser = argparse.ArgumentParser(description="刷新 L2 策略×状态矩阵")
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=config.REGIME_MATRIX_LOOKBACK_DAYS,
        help="regime 标签回看天数（默认 REGIME_MATRIX_LOOKBACK_DAYS）",
    )
    parser.add_argument(
        "--backtest-days",
        type=int,
        default=config.REGIME_MATRIX_BACKTEST_DAYS,
        help="单策略回测窗口（默认 REGIME_MATRIX_BACKTEST_DAYS）",
    )
    args = parser.parse_args()

    conn = sqlite3.connect(config.DB_PATH, timeout=300)
    run_migrations(conn)
    print(
        f"Refreshing matrix (lookback={args.lookback_days}, backtest={args.backtest_days})…"
    )
    r = refresh_strategy_regime_matrix(
        conn,
        lookback_days=args.lookback_days,
        backtest_days=args.backtest_days,
    )
    print(r)

    rows = conn.execute(
        """SELECT strategy_id, regime_bucket, sample_days, sharpe, ann_return_pct
           FROM strategy_regime_metrics
           WHERE regime_bucket='trend_down' AND as_of_date=?
           ORDER BY sample_days DESC, strategy_id""",
        (r.get("as_of_date"),),
    ).fetchall()
    print("\ntrend_down cells:")
    for row in rows:
        print(f"  {row[0]:20} sample={row[2]:3} sharpe={row[3]} ann={row[4]}%")

    m = get_strategy_regime_matrix(conn)
    print("\ncurrent bucket:", m.get("current_regime"))
    print("recommendation:", m.get("recommendation", {}).get("primary"))
    conn.close()


if __name__ == "__main__":
    main()
