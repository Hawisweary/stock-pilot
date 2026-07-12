"""新旧评分对比 — 五因子 composite vs 八维 comprehensive"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import date

from config import DB_PATH, DATA_DIR

DIFF_WARN_PCT = 30.0


def _load_pilot_codes() -> set[str] | None:
    manifest = os.path.join(DATA_DIR, "pilot_manifest.json")
    if not os.path.isfile(manifest):
        return None
    with open(manifest, encoding="utf-8") as f:
        m = json.load(f)
    return {s["code"] for s in m.get("stocks", [])}


def run_compare(db_path: str = None, pilot_only: bool = False) -> dict:
    path = db_path or DB_PATH
    if pilot_only:
        pilot_db = os.path.join(DATA_DIR, "afr_pilot.db")
        if os.path.isfile(pilot_db):
            path = pilot_db

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    pilot_codes = _load_pilot_codes() if pilot_only else None

    latest_fs = conn.execute("SELECT MAX(calc_date) FROM factor_scores").fetchone()[0]
    latest_cs = conn.execute("SELECT MAX(calc_date) FROM comprehensive_scores").fetchone()[0]

    rows = conn.execute(
        """SELECT s.code, s.name, s.industry_sw,
                  fs.composite_score AS old_composite,
                  cs.composite_score AS new_composite,
                  cs.fundamental_score
           FROM stocks s
           LEFT JOIN factor_scores fs ON fs.stock_id=s.id AND fs.calc_date=?
           LEFT JOIN comprehensive_scores cs ON cs.stock_id=s.id AND cs.calc_date=?
           WHERE s.is_active=1""",
        (latest_fs, latest_cs),
    ).fetchall()
    conn.close()

    pairs = []
    large_diff = []
    for r in rows:
        if pilot_codes and r["code"] not in pilot_codes:
            continue
        old_s, new_s = r["old_composite"], r["new_composite"]
        if old_s is None or new_s is None:
            continue
        diff = round(float(new_s) - float(old_s), 2)
        item = {
            "code": r["code"],
            "name": r["name"],
            "industry_sw": r["industry_sw"],
            "old_composite": round(float(old_s), 1),
            "new_composite": round(float(new_s), 1),
            "diff": diff,
            "fundamental_8d": round(float(r["fundamental_score"]), 1) if r["fundamental_score"] else None,
        }
        pairs.append(item)
        if abs(diff) > DIFF_WARN_PCT:
            large_diff.append(item)

    pairs.sort(key=lambda x: -abs(x["diff"]))
    diffs = [p["diff"] for p in pairs]
    mean_diff = round(sum(diffs) / len(diffs), 2) if diffs else 0
    mean_abs = round(sum(abs(d) for d in diffs) / len(diffs), 2) if diffs else 0

    return {
        "report_date": date.today().isoformat(),
        "db_path": path,
        "pilot_only": pilot_only,
        "factor_scores_date": latest_fs,
        "comprehensive_scores_date": latest_cs,
        "compared_count": len(pairs),
        "mean_diff": mean_diff,
        "mean_abs_diff": mean_abs,
        "large_diff_count": len(large_diff),
        "large_diff_threshold": DIFF_WARN_PCT,
        "rollback_warning": len(large_diff) > 100,
        "top_diff": pairs[:20],
        "large_diff_samples": large_diff[:20],
        "summary": (
            f"对比 {len(pairs)} 只；均值差 {mean_diff}；"
            f"|diff|>{DIFF_WARN_PCT} 共 {len(large_diff)} 只"
        ),
    }
