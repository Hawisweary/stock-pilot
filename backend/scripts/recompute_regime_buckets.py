#!/usr/bin/env python3
"""从七格 regime 重算四格 bucket（规则 v2 升级后一次性执行）。"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from migrations import run_migrations
from services.market_regime import recompute_regime_buckets


def main() -> None:
    conn = sqlite3.connect(config.DB_PATH, timeout=60)
    run_migrations(conn)
    r = recompute_regime_buckets(conn)
    conn.close()
    print("updated:", r["updated_rows"])
    print("CSI300 buckets:", r["bucket_distribution_csi300"])
    print("CSI800 buckets:", r["bucket_distribution_csi800"])
    print("bucket agreement:", r["bucket_agreement_pct"], "%")


if __name__ == "__main__":
    main()
