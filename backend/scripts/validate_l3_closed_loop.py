#!/usr/bin/env python3
"""P0：L1→L2→L3 四格闭环验证。"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from migrations import run_migrations
from services.market_regime import (
    REGIME_BUCKET_ORDER,
    REGIME_BUCKET_STRATEGY_MAP,
    get_regime_history,
    regime_bucket_label,
)
from services.strategy_regime_performance import (
    MIN_MATRIX_SAMPLES,
    _build_recommendation,
    get_strategy_regime_matrix,
)
from services.strategy_recommender import generate_current_recommendation


def _pick_date(conn: sqlite3.Connection, bucket: str) -> str | None:
    row = conn.execute(
        """SELECT trade_date FROM market_regime_daily
           WHERE regime_bucket_csi800=? ORDER BY trade_date DESC LIMIT 1""",
        (bucket,),
    ).fetchone()
    return row[0] if row else None


def main() -> None:
    conn = sqlite3.connect(config.DB_PATH, timeout=60)
    run_migrations(conn)

    print("=" * 60)
    print("P0 闭环验证 · L1→L2→L3")
    print(f"MIN_MATRIX_SAMPLES={MIN_MATRIX_SAMPLES}")
    print("=" * 60)

    hist = get_regime_history(conn, days=730)
    print(f"\n[L1] {hist['start_date']} → {hist['end_date']} confirmed: {hist['distribution']}")
    trend_segs = [s for s in hist.get("segments", []) if s["bucket"] in ("trend_up", "trend_down")]
    for s in trend_segs:
        print(f"  波段 {s['bucket_label']}: {s['start_date']} → {s['end_date']} ({s['days']}天)")

    matrix = get_strategy_regime_matrix(conn)
    cells = matrix.get("cells") or []
    print(f"\n[L2] as_of={matrix.get('as_of_date')} cells={len(cells)}")

    results: list[dict] = []
    for bucket in REGIME_BUCKET_ORDER:
        hard = REGIME_BUCKET_STRATEGY_MAP[bucket]
        sample_date = _pick_date(conn, bucket)
        regime_row = {}
        if sample_date:
            from services.market_regime import get_regime_for_date

            regime_row = get_regime_for_date(conn, sample_date)

        rec = _build_recommendation(cells, bucket, regime_row)
        primary = rec.get("primary") or {}
        sid = primary.get("strategy")
        hard_match = sid == hard
        results.append({
            "bucket": bucket,
            "bucket_label": regime_bucket_label(bucket),
            "hard_rule": hard,
            "sample_date": sample_date,
            "primary": sid,
            "primary_label": primary.get("label") or sid,
            "sharpe": primary.get("sharpe"),
            "sample_days": primary.get("sample_days"),
            "hard_match": hard_match,
            "confidence": rec.get("confidence"),
        })

        print(f"\n--- {regime_bucket_label(bucket)} ({bucket}) ---")
        print(f"  硬规则: {hard}")
        print(f"  样本日: {sample_date}")
        bc = [c for c in cells if c.get("bucket") == bucket and c.get("source") == "backtest"]
        for c in sorted(bc, key=lambda x: -(x.get("sharpe") or -999))[:3]:
            print(
                f"  矩阵 Top: {c.get('strategy')} sharpe={c.get('sharpe')} "
                f"days={c.get('sample_days')} sufficient={c.get('sample_sufficient')}"
            )
        if primary:
            flag = "✓ 硬规则一致" if hard_match else "△ 矩阵覆盖硬规则"
            print(
                f"  L3 主策略: {primary.get('label') or sid} sharpe={primary.get('sharpe')} "
                f"days={primary.get('sample_days')} {flag}"
            )
        else:
            print("  L3 主策略: ✗ 无推荐（样本不足）")

    live = generate_current_recommendation(conn, persist=False)
    rec = live.get("recommendation") or {}
    primary = rec.get("primary") or {}
    print("\n[L3 当前 live]")
    print(f"  日期: {live.get('trade_date')}")
    print(f"  状态: {rec.get('market', {}).get('regime_bucket_label') or rec.get('current_bucket_label')}")
    print(f"  主策略: {primary.get('label')} sharpe={primary.get('sharpe')} confidence={rec.get('confidence')}")

    ok = all(r["primary"] for r in results)
    td = next(r for r in results if r["bucket"] == "trend_down")
    if not td["primary"]:
        ok = False
    if td.get("primary") != "dividend_defensive":
        print("\n⚠ trend_down 未推荐 dividend_defensive")
        ok = False

    print("\n" + "=" * 60)
    print(json.dumps({"pass": ok, "buckets": results}, ensure_ascii=False, indent=2))
    print("=" * 60)
    conn.close()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
