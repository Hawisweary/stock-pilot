#!/usr/bin/env python3
"""数据对账 — 行情覆盖率、评分对齐率（G1 门禁）"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from config import DB_PATH, latest_trading_date  # noqa: E402

THRESHOLDS = {
    "quote_coverage_pct": 95.0,
    "score_fresh_pct": 90.0,
    "score_quote_align_pct": 98.0,
}


def run_audit(db_path: str = None) -> dict:
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    active = conn.execute("SELECT id, code FROM stocks WHERE is_active=1").fetchall()
    latest_quote = conn.execute(
        "SELECT MAX(trade_date) FROM stock_daily_quotes WHERE close IS NOT NULL"
    ).fetchone()[0]
    latest_score = conn.execute("SELECT MAX(calc_date) FROM comprehensive_scores").fetchone()[0]

    quote_ok = 0
    missing_quote: list[str] = []
    for s in active:
        row = conn.execute(
            """SELECT trade_date FROM stock_daily_quotes
               WHERE stock_id=? AND close IS NOT NULL ORDER BY trade_date DESC LIMIT 1""",
            (s["id"],),
        ).fetchone()
        if row and row[0] == latest_quote:
            quote_ok += 1
        else:
            missing_quote.append(s["code"])

    score_fresh = 0
    stale_scores: list[str] = []
    cutoff = (date.today() - timedelta(days=7)).strftime("%Y-%m-%d")
    for s in active:
        row = conn.execute(
            """SELECT calc_date FROM comprehensive_scores
               WHERE stock_id=? ORDER BY calc_date DESC LIMIT 1""",
            (s["id"],),
        ).fetchone()
        if row and row[0] >= cutoff:
            score_fresh += 1
        else:
            stale_scores.append(s["code"])

    align_ok = 0
    misaligned: list[str] = []
    score_rows = conn.execute(
        """SELECT s.code, cs.calc_date FROM comprehensive_scores cs
           JOIN stocks s ON cs.stock_id=s.id
           WHERE s.is_active=1 AND cs.calc_date=(SELECT MAX(calc_date) FROM comprehensive_scores)"""
    ).fetchall()
    for r in score_rows:
        q = conn.execute(
            """SELECT trade_date FROM stock_daily_quotes
               WHERE stock_id=(SELECT id FROM stocks WHERE code=?) AND trade_date <= ?
               ORDER BY trade_date DESC LIMIT 1""",
            (r["code"], r["calc_date"]),
        ).fetchone()
        if q:
            align_ok += 1
        else:
            misaligned.append(r["code"])

    n = max(len(active), 1)
    report = {
        "checked_at": date.today().isoformat(),
        "db_path": path,
        "latest_quote_date": latest_quote,
        "latest_score_date": latest_score,
        "latest_trading_date": latest_trading_date(path),
        "active_stocks": len(active),
        "quote_coverage_pct": round(quote_ok / n * 100, 2),
        "score_fresh_pct": round(score_fresh / n * 100, 2),
        "score_quote_align_pct": round(align_ok / max(len(score_rows), 1) * 100, 2),
        "missing_quote": missing_quote[:20],
        "stale_scores": stale_scores[:20],
        "misaligned": misaligned[:20],
        "gates": {},
    }

    passed = True
    for key, threshold in THRESHOLDS.items():
        ok = report[key] >= threshold
        report["gates"][key] = {"threshold": threshold, "value": report[key], "passed": ok}
        if not ok:
            passed = False
    report["passed"] = passed
    conn.close()
    return report


def main() -> int:
    db = sys.argv[1] if len(sys.argv) > 1 else DB_PATH
    report = run_audit(db)
    out_dir = os.path.join(ROOT, "docs", "reconciliation")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"data_audit_{date.today().isoformat()}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nReport saved: {out_path}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
