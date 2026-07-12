#!/usr/bin/env python3
"""阶段Ⅰ试点 — 选 50 只代表性股票 + 生成独立 pilot DB"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from config import DB_PATH, DATA_DIR  # noqa: E402

PILOT_DB = os.path.join(DATA_DIR, "afr_pilot.db")
MANIFEST = os.path.join(DATA_DIR, "pilot_manifest.json")
TARGET = 50


def select_pilot_stocks(conn: sqlite3.Connection, limit: int = TARGET) -> list[dict]:
    """每个申万一级行业优先 1 只（按综合分），再补足至 limit"""
    conn.row_factory = sqlite3.Row
    latest = conn.execute("SELECT MAX(calc_date) FROM comprehensive_scores").fetchone()[0]
    rows = conn.execute(
        """SELECT s.id, s.code, s.name, s.industry_sw, cs.composite_score
           FROM stocks s
           LEFT JOIN comprehensive_scores cs ON cs.stock_id=s.id AND cs.calc_date=?
           WHERE s.is_active=1
           ORDER BY (cs.composite_score IS NULL), cs.composite_score DESC""",
        (latest,),
    ).fetchall()

    picked: list[dict] = []
    seen_ind: set[str] = set()
    for r in rows:
        ind = (r["industry_sw"] or "未分类").strip() or "未分类"
        if ind not in seen_ind:
            seen_ind.add(ind)
            picked.append(dict(r))
        if len(picked) >= limit:
            break

    if len(picked) < limit:
        have = {p["id"] for p in picked}
        for r in rows:
            if r["id"] in have:
                continue
            picked.append(dict(r))
            have.add(r["id"])
            if len(picked) >= limit:
                break
    return picked[:limit]


def build_pilot_db(stock_ids: list[int], src: str, dst: str) -> None:
    """复制主库为 pilot，仅保留试点股票相关行（核心表）"""
    if os.path.isfile(dst):
        os.remove(dst)
    shutil.copy2(src, dst)
    conn = sqlite3.connect(dst)
    placeholders = ",".join("?" * len(stock_ids))
    id_set = set(stock_ids)

    # 非试点股票标记 inactive
    conn.execute(
        f"UPDATE stocks SET is_active=0 WHERE id NOT IN ({placeholders})",
        stock_ids,
    )

    tables_with_stock_id = [
        "stock_daily_quotes",
        "comprehensive_scores",
        "factor_scores",
        "factor_values",
        "financial_indicators",
        "financial_reports",
        "capital_scores",
        "policy_scores",
        "sentiment_scores",
        "valuation_scores",
        "tech_analysis_cache",
        "stock_news",
        "valuation_snapshots",
        "ml_predictions",
    ]
    for table in tables_with_stock_id:
        try:
            conn.execute(f"DELETE FROM {table} WHERE stock_id NOT IN ({placeholders})", stock_ids)
        except sqlite3.OperationalError:
            pass

    conn.commit()
    conn.close()


def main() -> int:
    if not os.path.isfile(DB_PATH):
        print(f"主库不存在: {DB_PATH}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(DB_PATH)
    stocks = select_pilot_stocks(conn)
    conn.close()

    if len(stocks) < 10:
        print(f"活跃股票不足，仅选到 {len(stocks)} 只", file=sys.stderr)
        return 1

    ids = [s["id"] for s in stocks]
    build_pilot_db(ids, DB_PATH, PILOT_DB)

    industries = sorted({(s.get("industry_sw") or "未分类") for s in stocks})
    manifest = {
        "created_at": date.today().isoformat(),
        "pilot_db": PILOT_DB,
        "source_db": DB_PATH,
        "stock_count": len(stocks),
        "industry_count": len(industries),
        "industries": industries,
        "stocks": [{"id": s["id"], "code": s["code"], "name": s["name"], "industry_sw": s.get("industry_sw")} for s in stocks],
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(json.dumps({"pilot_db": PILOT_DB, "manifest": MANIFEST, "stock_count": len(stocks), "industries": len(industries)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
