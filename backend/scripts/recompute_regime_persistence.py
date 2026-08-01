#!/usr/bin/env python3
"""对已有 market_regime_daily 重算状态持续性确认（raw → confirmed）。"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from migrations import run_migrations
from services.market_regime import recompute_regime_persistence


def main() -> None:
    parser = argparse.ArgumentParser(description="重算 L1 状态持续性确认")
    parser.add_argument("--days", type=int, default=730)
    parser.add_argument("--persistence-days", type=int, default=None)
    args = parser.parse_args()

    conn = sqlite3.connect(config.DB_PATH, timeout=120)
    run_migrations(conn)
    result = recompute_regime_persistence(
        conn,
        days=args.days,
        min_days=args.persistence_days,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    conn.close()


if __name__ == "__main__":
    main()
