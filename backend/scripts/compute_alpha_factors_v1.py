"""计算 Alpha 因子 v1：盈余惊喜（全量重算）+ 资金共振（按交易日回补）。

行业中性估值不需要预计算，是从已有 valuation_scores 现读现算的（见 alpha_factors_v1.py）。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from services.alpha_factors_v1 import compute_capital_resonance, compute_earnings_surprise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=10, help="资金共振回补最近 N 个交易日（默认10）")
    parser.add_argument("--skip-surprise", action="store_true")
    parser.add_argument("--skip-resonance", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(config.DB_PATH)

    if not args.skip_surprise:
        n = compute_earnings_surprise(conn)
        print(f"[盈余惊喜] 共写入 {n} 条")

    if not args.skip_resonance:
        rows = conn.execute(
            """SELECT DISTINCT trade_date FROM stock_moneyflow_l2_daily
               WHERE trade_date >= ? ORDER BY trade_date""",
            ((date.today() - timedelta(days=args.days * 2)).isoformat(),),
        ).fetchall()
        trading_days = [r[0] for r in rows][-args.days:]
        total = 0
        for i, d in enumerate(trading_days, 1):
            n = compute_capital_resonance(conn, d)
            total += n
            print(f"[资金共振] [{i}/{len(trading_days)}] {d}: {n} 只")
        print(f"[资金共振] 共写入 {total} 条")

    conn.close()


if __name__ == "__main__":
    main()
