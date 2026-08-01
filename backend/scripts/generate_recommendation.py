#!/usr/bin/env python3
"""生成并落库 L3 策略推荐。"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from migrations import run_migrations
from services.strategy_recommender import generate_and_persist_recommendation


def main() -> None:
    conn = sqlite3.connect(config.DB_PATH, timeout=120)
    run_migrations(conn)
    r = generate_and_persist_recommendation(conn)
    rec = r.get("recommendation") or {}
    primary = rec.get("primary") or {}
    print(json.dumps({
        "trade_date": r.get("trade_date"),
        "bucket": (r.get("market") or {}).get("regime_bucket_label"),
        "confidence": rec.get("confidence"),
        "primary": primary.get("label"),
        "sharpe": primary.get("sharpe"),
        "top_picks": [p.get("name") for p in (primary.get("top_picks") or [])[:5]],
    }, ensure_ascii=False, indent=2))
    conn.close()


if __name__ == "__main__":
    main()
