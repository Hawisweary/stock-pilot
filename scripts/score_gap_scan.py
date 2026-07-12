#!/usr/bin/env python3
"""CLI：扫描 comprehensive 维度缺口"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import config

config.DB_PATH = os.environ.get("AFR_DB_PATH", os.path.join(ROOT, "data", "afr.db"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan comprehensive dimension gaps")
    parser.add_argument("--target-date", dest="target_date", default=None)
    parser.add_argument("--stock-ids", dest="stock_ids", default=None, help="逗号分隔 stock_id")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    stock_ids = None
    if args.stock_ids:
        stock_ids = [int(x.strip()) for x in args.stock_ids.split(",") if x.strip()]

    from services.score_gap_scanner import scan_gaps

    report = scan_gaps(target_date=args.target_date, stock_ids=stock_ids)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"target_date: {report['target_date']}")
        print(f"active_stocks: {report['active_stocks_count']}")
        print(f"sync_rate_all: {report['sync_rate_all']:.1%}")
        print(f"sync_rate_required: {report['sync_rate_required']:.1%}")
        print(f"missing_total: {report['missing_total']}")
        for dim, s in report.get("summary", {}).items():
            if s.get("missing") or s.get("no_source"):
                print(f"  {dim}: ok={s['ok']} missing={s['missing']} no_source={s['no_source']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
