#!/usr/bin/env python3
"""P3-E：Jump Model vs 规则 L1 对照 + 可选落库。

用法:
  cd backend && python scripts/compare_regime_jump.py
  cd backend && python scripts/compare_regime_jump.py --penalties 25,50,75,100
  cd backend && python scripts/compare_regime_jump.py --persist --penalty 50
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from migrations import run_migrations
from services.regime_jump import (
    compare_jump_vs_rules,
    fit_and_persist_full_sample,
    format_compare_report_text,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Jump Model vs 规则 L1 对照")
    parser.add_argument("--days", type=int, default=730)
    parser.add_argument("--train-ratio", type=float, default=0.85)
    parser.add_argument(
        "--penalties",
        type=str,
        default="25,50,75,100",
        help="跳跃惩罚 λ 扫描（逗号分隔）",
    )
    parser.add_argument("--penalty", type=float, default=50.0, help="--persist 时使用的 λ")
    parser.add_argument(
        "--backend",
        choices=("auto", "jumpmodels", "simple"),
        default="auto",
        help="auto=优先 jumpmodels，3.9 回退 simple DP",
    )
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    penalties = tuple(float(x.strip()) for x in args.penalties.split(",") if x.strip())

    conn = sqlite3.connect(config.DB_PATH, timeout=120)
    run_migrations(conn)

    report = compare_jump_vs_rules(
        conn,
        days=max(90, min(args.days, 730)),
        train_ratio=max(0.6, min(args.train_ratio, 0.95)),
        penalties=penalties,
        backend=args.backend,
    )

    if args.persist and not report.get("error"):
        lam = args.penalty
        if report.get("recommended_penalty") is not None:
            lam = float(report["recommended_penalty"])
        report["persist"] = fit_and_persist_full_sample(
            conn, days=args.days, jump_penalty=lam, backend=args.backend,
        )

    conn.close()

    if report.get("error"):
        print(report["error"], file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_compare_report_text(report))
        if report.get("persist"):
            print("\n[persist]", json.dumps(report["persist"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
