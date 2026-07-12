#!/usr/bin/env python3
"""重算十维显示为 — 的标的：慢路径财报 → 行业 → V5 metrics → 八维补算 → V5 综合分。"""
from __future__ import annotations

import json
import os
import sys

if sys.version_info < (3, 10):
    print("需要 Python 3.10+：bash backend/scripts/run_py.sh scripts/run_v5_recalc_dash.py")
    raise SystemExit(1)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed

import database
from config import DB_PATH, latest_trading_date
from services import fetch_job

DASH_DIMS = ("quality", "industry", "capital", "valuation", "technical", "policy")
COL_MAP = {
    "quality": "quality_score",
    "industry": "industry_score",
    "capital": "capital_score",
    "valuation": "val_score",
    "technical": "technical_score",
    "policy": "policy_score",
}


def _latest_scores(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """SELECT s.id, s.code, s.market, s.industry_sw2,
                  cs.quality_score, cs.industry_score, cs.capital_score,
                  cs.val_score, cs.technical_score, cs.policy_score,
                  cs.v5_breakdown_json, cs.composite_v5
           FROM stocks s
           LEFT JOIN (
             SELECT cs.* FROM comprehensive_scores cs
             INNER JOIN (
               SELECT stock_id, MAX(calc_date) md FROM comprehensive_scores GROUP BY stock_id
             ) x ON cs.stock_id=x.stock_id AND cs.calc_date=x.md
           ) cs ON cs.stock_id=s.id
           WHERE s.is_active=1
           ORDER BY s.id"""
    ).fetchall()


def _tier_missing(row: sqlite3.Row, dim: str) -> bool:
    col = COL_MAP[dim]
    if row[col] is not None:
        return False
    raw = row["v5_breakdown_json"]
    if not raw:
        return True
    try:
        tiers = json.loads(raw).get("tiers") or {}
        return tiers.get(dim) is None
    except Exception:
        return True


def stocks_with_dash(conn: sqlite3.Connection) -> tuple[list[int], dict[str, list[str]]]:
    """返回需重算 stock_id 列表 + 各维度缺失代码。"""
    by_dim: dict[str, list[str]] = {d: [] for d in DASH_DIMS}
    ids: set[int] = set()
    for row in _latest_scores(conn):
        sid, code = int(row["id"]), row["code"]
        for dim in DASH_DIMS:
            if _tier_missing(row, dim):
                by_dim[dim].append(code)
                ids.add(sid)
    return sorted(ids), by_dim


def _missing_cfo(conn: sqlite3.Connection, scope: list[int]) -> list[tuple[int, str, str]]:
    if not scope:
        return []
    ph = ",".join("?" * len(scope))
    rows = conn.execute(
        f"""SELECT s.id, s.code, s.market FROM stocks s
            WHERE s.id IN ({ph}) AND s.is_active=1
            AND NOT EXISTS (
              SELECT 1 FROM financial_reports f
              WHERE f.stock_id=s.id AND f.operating_cf IS NOT NULL
            )""",
        scope,
    ).fetchall()
    return [(int(r[0]), r[1], r[2] or "A") for r in rows]


def _refetch_slow(targets: list[tuple[int, str, str]], parallel: int = 3) -> dict:
    ok, fail = [], []

    def _one(t: tuple[int, str, str]) -> tuple[str, bool]:
        sid, code, market = t
        try:
            fetch_job.start_job(sid)
            r = fetch_job.sync_fetch_one(sid, code, market, finance_fast=False)
            fetch_job.complete_job(sid, r, auto_score=False)
            return code, r.get("status") in ("success", "partial", "done")
        except Exception:
            fetch_job.fail_job(sid, "slow refetch failed")
            return code, False

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        for fut in as_completed({pool.submit(_one, t): t for t in targets}):
            code, success = fut.result()
            (ok if success else fail).append(code)
    return {"ok": len(ok), "fail": fail}


def main() -> int:
    database.init()
    conn = sqlite3.connect(DB_PATH, timeout=120)
    scope, by_dim = stocks_with_dash(conn)
    conn.close()

    print(f"待重算 {len(scope)} 只（十维含 —）")
    for dim in DASH_DIMS:
        print(f"  {dim}: {len(by_dim[dim])} 只")

    if not scope:
        print("无缺失维度，退出")
        return 0

    out: dict = {"scope_count": len(scope), "by_dim": {k: len(v) for k, v in by_dim.items()}}

    # 1) 行业
    from services.industry_l2_sync import sync_industry_l2

    out["industry_l2"] = sync_industry_l2(scope, limit=500)

    # 2) 慢路径财报（质量因子依赖现金流）
    conn = sqlite3.connect(DB_PATH)
    cfo_targets = _missing_cfo(conn, scope)
    conn.close()
    out["cfo_refetch_targets"] = len(cfo_targets)
    if cfo_targets:
        out["cfo_refetch"] = _refetch_slow(cfo_targets, parallel=3)

    # 3) 主力流（可能仍为 0，但尝试）
    from services.fund_flow_sync import sync_stock_fund_flow

    out["fund_flow"] = sync_stock_fund_flow(scope)

    # 4) V5 数据源
    from services.v5_data_sync import sync_v5_data_sources

    out["v5_sync"] = sync_v5_data_sources(
        stock_ids=scope,
        skip_macro=True,
        skip_fund_flow=True,
        skip_sector=False,
        skip_metrics=False,
        skip_industry_l2=True,
        skip_eps_revision=False,
        skip_announcements=True,
        skip_news_fetch=False,
        skip_events=True,
        skip_risk=False,
        skip_policy=False,
        skip_mood=False,
        skip_v5_scores=False,
        reclassify_events=False,
        use_llm_events=False,
    )

    # 5) 八维补算 + 技术
    from services.batch_score_orchestrator import fill_gaps
    from services.batch_score_compute import compute_technical

    dt = latest_trading_date()
    out["technical"] = compute_technical(scope, dt)
    out["batch_fill"] = fill_gaps(
        mode="compute_and_sync",
        stock_ids=scope,
        skip_no_source=True,
        triggered_by="recalc_dash",
    )

    # 6) 再次写 V5 十维（覆盖 batch 后的估值/技术列）
    from services.v5_scorer import compute_all_v5_scores

    out["v5_rescore"] = compute_all_v5_scores(scope, calc_date=dt)

    # 7) 复检
    conn = sqlite3.connect(DB_PATH)
    scope2, by_dim2 = stocks_with_dash(conn)
    still = []
    for row in _latest_scores(conn):
        if int(row["id"]) not in scope:
            continue
        miss = [d for d in DASH_DIMS if _tier_missing(row, d)]
        if miss:
            still.append({"code": row["code"], "v5": row["composite_v5"], "missing": miss})
    conn.close()

    out["still_dash_count"] = len(scope2)
    out["still_dash_by_dim"] = {k: len(v) for k, v in by_dim2.items()}
    out["still_dash_samples"] = still[:25]

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
