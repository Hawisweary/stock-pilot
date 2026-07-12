#!/usr/bin/env python3
"""新旧评分对比报告 CLI"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from datetime import date  # noqa: E402
from services.score_compare import run_compare  # noqa: E402


def main() -> int:
    pilot_only = "--pilot" in sys.argv
    db = next((a for a in sys.argv[1:] if a.endswith(".db")), None)
    report = run_compare(db_path=db, pilot_only=pilot_only)
    out_dir = os.path.join(ROOT, "docs", "reconciliation")
    os.makedirs(out_dir, exist_ok=True)
    suffix = "pilot" if pilot_only else "full"
    out_path = os.path.join(out_dir, f"score_compare_{suffix}_{date.today().isoformat()}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nSaved: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
