#!/usr/bin/env python3
"""P3-A：Regime Persistence A/B 对照（对称 vs 不对称 vs 对称3日）。

默认仅内存模拟，不写库。选定方案后：
  python scripts/compare_regime_persistence_ab.py --apply asymmetric_prod

用法:
  cd backend && python scripts/compare_regime_persistence_ab.py
  cd backend && python scripts/compare_regime_persistence_ab.py --days 730 --fast
  cd backend && python scripts/compare_regime_persistence_ab.py --variants symmetric_5,asymmetric_prod
  cd backend && python scripts/compare_regime_persistence_ab.py --apply asymmetric_prod --refresh-l2-l3
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
from services.regime_persistence_ab import (
    DEFAULT_VARIANTS,
    apply_variant_to_db,
    compare_persistence_variants,
    format_ab_report_text,
    variant_by_id,
)


def _parse_variants(spec: str | None) -> list:
    if not spec:
        return list(DEFAULT_VARIANTS)
    out = []
    for part in spec.split(","):
        vid = part.strip()
        if not vid:
            continue
        v = variant_by_id(vid)
        if not v:
            raise SystemExit(f"未知 variant: {vid}，可选: symmetric_5, asymmetric_prod, symmetric_3")
        out.append(v)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Regime Persistence A/B 对照")
    parser.add_argument("--days", type=int, default=730)
    parser.add_argument("--l3-sim-days", type=int, default=365)
    parser.add_argument("--variants", type=str, default=None, help="逗号分隔，如 symmetric_5,asymmetric_prod")
    parser.add_argument("--fast", action="store_true", help="跳过 L3 切换模拟（更快）")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--apply",
        type=str,
        default=None,
        metavar="VARIANT_ID",
        help="将 variant 写回 DB（如 asymmetric_prod）",
    )
    parser.add_argument(
        "--refresh-l2-l3",
        action="store_true",
        help="与 --apply 联用：写库后刷新 L2 矩阵 + L3 推荐",
    )
    args = parser.parse_args()

    conn = sqlite3.connect(config.DB_PATH, timeout=120)
    conn.execute("PRAGMA busy_timeout=60000")
    run_migrations(conn)

    if args.apply:
        variant = variant_by_id(args.apply)
        if not variant:
            print(f"未知 variant: {args.apply}", file=sys.stderr)
            conn.close()
            return 1
        print(f"[apply] {variant.label} ({variant.id})…")
        applied = apply_variant_to_db(conn, variant, days=args.days)
        print(json.dumps(applied, ensure_ascii=False, indent=2))
        if args.refresh_l2_l3:
            from services.regime_pipeline import run_regime_l2_l3_pipeline

            print("[refresh] L2 + L3…")
            pipe = run_regime_l2_l3_pipeline(
                conn, skip_regime=True, refresh_matrix=True,
            )
            print(json.dumps(pipe, ensure_ascii=False, indent=2))
        conn.close()
        return 0

    variants = _parse_variants(args.variants)
    report = compare_persistence_variants(
        conn,
        variants=variants,
        days=max(60, min(args.days, 730)),
        l3_sim_days=max(90, min(args.l3_sim_days, 730)),
        run_l3_sim=not args.fast,
    )
    conn.close()

    if report.get("error"):
        print(report["error"], file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_ab_report_text(report))

    return 0


if __name__ == "__main__":
    sys.exit(main())
