#!/usr/bin/env python3
"""市场状态划分验证 — 三层报告 CLI。

用法:
  cd backend && python scripts/validate_regime.py
  cd backend && python scripts/validate_regime.py --primary csi800 --days 365
  cd backend && python scripts/validate_regime.py --include-strategy --strategy-days 180
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from migrations import run_migrations
from services.regime_validation import format_validation_report_text, generate_validation_report


def main() -> None:
    parser = argparse.ArgumentParser(description="市场状态划分验证报告")
    parser.add_argument("--primary", choices=("csi300", "csi800"), default="csi800")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--include-strategy", action="store_true", help="含第三层策略条件回测（较慢）")
    parser.add_argument("--strategy-days", type=int, default=180)
    parser.add_argument("--include-l3-sim", action="store_true", help="含 L3 策略切换 walk-forward 模拟")
    parser.add_argument("--l3-sim-days", type=int, default=365)
    parser.add_argument("--include-hmm", action="store_true", help="含 HMM vs 规则 L1 对照")
    parser.add_argument("--json", action="store_true", help="输出 JSON 而非文本")
    args = parser.parse_args()

    conn = sqlite3.connect(config.DB_PATH, timeout=60)
    run_migrations(conn)

    report = generate_validation_report(
        conn,
        primary=args.primary,
        days=args.days,
        include_strategy=args.include_strategy,
        strategy_days=args.strategy_days,
        include_l3_sim=args.include_l3_sim,
        l3_sim_days=args.l3_sim_days,
    )
    if args.include_hmm:
        from services.regime_hmm import compare_hmm_vs_rules

        report["layer_hmm_vs_rules"] = compare_hmm_vs_rules(conn, days=args.days)
    conn.close()

    if args.json:
        import json
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_validation_report_text(report))


if __name__ == "__main__":
    main()
