#!/usr/bin/env python3
"""P3-D：K-Means / GMM vs 规则 L1 对照 + 可选落库。

用法:
  cd backend && python scripts/compare_regime_cluster.py
  cd backend && python scripts/compare_regime_cluster.py --method gmm
  cd backend && python scripts/compare_regime_cluster.py --persist --json
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
from services.regime_cluster import (
    compare_cluster_vs_rules,
    fit_and_persist_full_sample,
    format_compare_report_text,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="K-Means / GMM vs 规则 L1 对照")
    parser.add_argument("--days", type=int, default=730)
    parser.add_argument("--train-ratio", type=float, default=0.85)
    parser.add_argument(
        "--method",
        choices=("kmeans", "gmm", "both"),
        default="both",
        help="对照算法（默认两者）",
    )
    parser.add_argument(
        "--persist",
        action="store_true",
        help="全样本拟合后写入 market_regime_cluster_daily",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    methods = ("kmeans", "gmm") if args.method == "both" else (args.method,)

    conn = sqlite3.connect(config.DB_PATH, timeout=120)
    run_migrations(conn)

    report = compare_cluster_vs_rules(
        conn,
        days=max(90, min(args.days, 730)),
        train_ratio=max(0.6, min(args.train_ratio, 0.95)),
        methods=methods,
    )

    if args.persist and not report.get("error"):
        report["persist"] = fit_and_persist_full_sample(conn, days=args.days, methods=methods)

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
