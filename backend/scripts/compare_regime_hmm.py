#!/usr/bin/env python3
"""P3-C：HMM vs 规则 L1 对照 + 可选落库。

用法:
  cd backend && python scripts/compare_regime_hmm.py
  cd backend && python scripts/compare_regime_hmm.py --persist
  cd backend && python scripts/compare_regime_hmm.py --json
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
from services.regime_hmm import (
    compare_hmm_vs_rules,
    fit_and_persist_full_sample,
    format_compare_report_text,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="HMM vs 规则 L1 对照")
    parser.add_argument("--days", type=int, default=730)
    parser.add_argument("--train-ratio", type=float, default=0.85)
    parser.add_argument("--persist", action="store_true", help="全样本拟合后写入 market_regime_hmm_daily")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(config.DB_PATH, timeout=120)
    run_migrations(conn)

    report = compare_hmm_vs_rules(
        conn, days=max(90, min(args.days, 730)), train_ratio=max(0.6, min(args.train_ratio, 0.95)),
    )

    if args.persist and not report.get("error"):
        persisted = fit_and_persist_full_sample(conn, days=args.days)
        report["persist"] = persisted

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
