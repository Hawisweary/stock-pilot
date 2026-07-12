#!/usr/bin/env python3
"""CLI: 扩展因子历史 + 利息保障补全"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from services.factor_history_expand import expand_factor_history  # noqa: E402
from services.financial_backfill import backfill_interest_coverage  # noqa: E402


def main() -> int:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    ic = backfill_interest_coverage()
    fac = expand_factor_history(days=days)
    report = {"interest_coverage": ic, "factor_history": fac}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
