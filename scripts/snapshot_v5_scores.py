#!/usr/bin/env python3
"""
snapshot_v5_scores.py — 快照当前 composite_v5 Top-N 分数

用法：
    python scripts/snapshot_v5_scores.py [--top 100] [--out docs/reconciliation/baseline_YYYYMMDD.json]

产出文件供 v5_release_gate.py 比对。每次 Phase 前运行一次。
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import date

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(BASE_DIR, "data", "afr.db")
DEFAULT_OUT_DIR = os.path.join(BASE_DIR, "docs", "reconciliation")


def snapshot(db_path: str, top_n: int, out_path: str) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """
        SELECT
            s.id            AS stock_id,
            s.code,
            s.name,
            cs.calc_date,
            cs.composite_v5 AS score,
            cs.veto_status,
            cs.v5_breakdown_json
        FROM stocks s
        INNER JOIN comprehensive_scores cs ON cs.stock_id = s.id
        INNER JOIN (
            SELECT stock_id, MAX(calc_date) AS md
            FROM comprehensive_scores
            WHERE composite_v5 IS NOT NULL
            GROUP BY stock_id
        ) latest ON cs.stock_id = latest.stock_id AND cs.calc_date = latest.md
        WHERE s.is_active = 1
        ORDER BY cs.composite_v5 DESC
        LIMIT ?
        """,
        (top_n,),
    ).fetchall()

    conn.close()

    stocks = []
    for r in rows:
        breakdown = None
        if r["v5_breakdown_json"]:
            try:
                breakdown = json.loads(r["v5_breakdown_json"])
            except Exception:
                breakdown = None
        stocks.append(
            {
                "stock_id": r["stock_id"],
                "code": r["code"],
                "name": r["name"],
                "calc_date": r["calc_date"],
                "score": r["score"],
                "veto_status": r["veto_status"],
                "v5_breakdown": breakdown,
            }
        )

    payload = {
        "snapshot_date": date.today().isoformat(),
        "db_path": db_path,
        "top_n": top_n,
        "total_captured": len(stocks),
        "stocks": stocks,
    }

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return payload


def main():
    parser = argparse.ArgumentParser(description="快照 composite_v5 Top-N 分数")
    parser.add_argument("--top", type=int, default=100, help="快照股票数量（默认 100）")
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="输出 JSON 路径（默认 docs/reconciliation/baseline_YYYYMMDD.json）",
    )
    parser.add_argument("--db", type=str, default=DEFAULT_DB, help="数据库路径")
    args = parser.parse_args()

    out_path = args.out or os.path.join(
        DEFAULT_OUT_DIR, f"baseline_{date.today().strftime('%Y%m%d')}.json"
    )

    if not os.path.exists(args.db):
        print(f"[ERROR] 数据库不存在: {args.db}", file=sys.stderr)
        sys.exit(1)

    payload = snapshot(args.db, args.top, out_path)
    print(f"[OK] 快照完成：{payload['total_captured']} 只股票 → {out_path}")

    if payload["total_captured"] == 0:
        print("[WARN] composite_v5 为空，请先运行 V5 重算", file=sys.stderr)


if __name__ == "__main__":
    main()
