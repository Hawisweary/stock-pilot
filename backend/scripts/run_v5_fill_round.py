#!/usr/bin/env python3
"""V5 十维补全一轮：行业 → 财报重抓 → 资金流 → V5 同步 → 八维 batch-fill。"""
from __future__ import annotations

import json
import sqlite3
import sys

if sys.version_info < (3, 10):
    print("需要 Python 3.10+，请用: bash backend/scripts/run_py.sh scripts/run_v5_fill_round.py")
    raise SystemExit(1)

import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
from config import DB_PATH
from concurrent.futures import ThreadPoolExecutor, as_completed
from services import fetch_job


def _new_stock_ids(conn: sqlite3.Connection) -> list[int]:
    """近期扩池标的（id>=71 或 茅台 id=1 重激活）。"""
    rows = conn.execute(
        """SELECT id FROM stocks WHERE is_active=1 AND (id >= 71 OR id = 1) ORDER BY id"""
    ).fetchall()
    return [int(r[0]) for r in rows]


def _missing_cfo_ids(conn: sqlite3.Connection, scope: list[int] | None) -> list[int]:
    ph = ""
    params: tuple = ()
    if scope:
        ph = f" AND s.id IN ({','.join('?' * len(scope))})"
        params = tuple(scope)
    rows = conn.execute(
        f"""SELECT s.id, s.code, s.market FROM stocks s
            WHERE s.is_active=1 {ph}
            AND NOT EXISTS (
              SELECT 1 FROM financial_reports f
              WHERE f.stock_id=s.id AND f.operating_cf IS NOT NULL
            )""",
        params,
    ).fetchall()
    return [(int(r[0]), r[1], r[2] or "A") for r in rows]


def _refetch_financials(targets: list[tuple[int, str, str]], parallel: int = 4) -> dict:
    ok, fail = [], []

    def _one(item: tuple[int, str, str]) -> tuple[int, str, bool]:
        sid, code, market = item
        try:
            fetch_job.start_job(sid)
            r = fetch_job.sync_fetch_one(sid, code, market, finance_fast=True)
            fetch_job.complete_job(sid, r, auto_score=False)
            return sid, code, r.get("status") != "error"
        except Exception:
            fetch_job.fail_job(sid, "refetch failed")
            return sid, code, False

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futs = {pool.submit(_one, t): t for t in targets}
        for fut in as_completed(futs):
            sid, code, success = fut.result()
            (ok if success else fail).append(code)

    return {"ok": len(ok), "fail": fail, "codes_ok": ok[:10]}


def main() -> int:
    database.init()
    conn = sqlite3.connect(DB_PATH)
    scope = _new_stock_ids(conn)
    conn.close()

    out: dict = {"scope_ids": len(scope)}

    # 1) 行业二级
    from services.industry_l2_sync import sync_industry_l2

    ind = sync_industry_l2(scope, limit=200)
    out["industry_l2"] = ind

    # 2) 缺现金流财报 → 重抓
    conn = sqlite3.connect(DB_PATH)
    refetch_targets = _missing_cfo_ids(conn, scope)
    conn.close()
    out["refetch_targets"] = len(refetch_targets)
    if refetch_targets:
        out["refetch"] = _refetch_financials(refetch_targets, parallel=4)

    # 3) 个股主力流
    from services.fund_flow_sync import sync_stock_fund_flow

    out["fund_flow"] = sync_stock_fund_flow(scope)

    # 4) V5 数据源 + 评分
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

    # 5) 八维 batch-fill
    from services.batch_score_orchestrator import fill_gaps

    out["batch_fill"] = fill_gaps(
        mode="compute_and_sync",
        stock_ids=scope,
        skip_no_source=True,
        triggered_by="v5_fill_round",
    )

    # 6) 抽样：莱斯信息
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """SELECT composite_v5, fundamental_score, quality_score, industry_score,
                  capital_score, val_score, technical_score, policy_score,
                  sentiment_score, mood_score, market_env_score, v5_breakdown_json
           FROM comprehensive_scores WHERE stock_id=73
           ORDER BY calc_date DESC LIMIT 1"""
    ).fetchone()
    m = conn.execute(
        """SELECT growth_tier, quality_tier, revenue_yoy_q, cfo_np
           FROM stock_v5_metrics WHERE stock_id=73 ORDER BY calc_date DESC LIMIT 1"""
    ).fetchone()
    ind2 = conn.execute("SELECT industry_sw2 FROM stocks WHERE id=73").fetchone()
    cfo = conn.execute(
        """SELECT COUNT(*) FROM financial_reports
           WHERE stock_id=73 AND operating_cf IS NOT NULL"""
    ).fetchone()[0]
    flow = conn.execute(
        """SELECT main_net_5d FROM stock_fund_flow_daily
           WHERE stock_id=73 ORDER BY trade_date DESC LIMIT 1"""
    ).fetchone()
    conn.close()

    out["sample_688631"] = {
        "industry_sw2": ind2[0] if ind2 else None,
        "cfo_rows": cfo,
        "main_net_5d": flow[0] if flow else None,
        "v5_metrics": dict(m) if m else None,
        "scores": dict(row) if row else None,
    }
    if row and row["v5_breakdown_json"]:
        try:
            out["sample_688631"]["breakdown"] = json.loads(row["v5_breakdown_json"])
        except Exception:
            pass

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
