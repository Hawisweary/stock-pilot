#!/usr/bin/env python3
"""S0 因子数据质量初始化 — adj_close / lifecycle / calendar / wide 迁移"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main():
    parser = argparse.ArgumentParser(description="Run factor lab S0 setup")
    parser.add_argument("--no-wide", action="store_true", help="跳过 EAV→宽表迁移")
    args = parser.parse_args()

    from services.factor_s0_setup import run_factor_s0_setup

    result = run_factor_s0_setup(migrate_wide=not args.no_wide)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
