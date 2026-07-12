#!/usr/bin/env python3
"""一键：sentiment 补源 + stale 刷新 + 验收"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import config

config.DB_PATH = os.environ.get("AFR_DB_PATH", os.path.join(ROOT, "data", "afr.db"))

import database as db

db.init()


def main() -> int:
    from services.batch_score_maintenance import retry_sentiment_gaps, sync_gaps_after_fetch
    from services.score_gap_scanner import scan_gaps

    before = scan_gaps()
    print("=== Before ===")
    print(json.dumps(
        {
            "sync_rate_all": before["sync_rate_all"],
            "stale_total": before["stale_total"],
            "sentiment": before["summary"]["sentiment_score"],
        },
        ensure_ascii=False,
        indent=2,
    ))

    print("\n=== Step 1: sentiment fetch + score ===")
    s1 = retry_sentiment_gaps()
    print(json.dumps(s1, ensure_ascii=False, indent=2, default=str))

    print("\n=== Step 2: sync remaining stale ===")
    s2 = sync_gaps_after_fetch()
    print(json.dumps(s2, ensure_ascii=False, indent=2, default=str))

    after = scan_gaps()
    print("\n=== After ===")
    print(json.dumps(
        {
            "sync_rate_all": after["sync_rate_all"],
            "stale_total": after["stale_total"],
            "sentiment": after["summary"]["sentiment_score"],
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0 if after.get("stale_total", 99) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
