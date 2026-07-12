"""
新股票 onboard 编排 — register → prefetch → fetch → factor → batch-fill
"""
from __future__ import annotations

import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import config
from api_utils import execute_insert, execute_sql, execute_update
from config import DB_PATH
from services import fetch_job

ONBOARD_JOB_TYPE = "stock_onboard"


def register_stock(code: str, market: str = "A", skip_existing: bool = True) -> dict:
    code = str(code).strip()
    if not code:
        return {"code": code, "status": "error", "reason": "empty code"}

    existing = execute_sql(
        "SELECT id, is_active FROM stocks WHERE code=? AND market=?",
        (code, market),
    )
    if existing:
        row = existing[0]
        if row["is_active"]:
            if skip_existing:
                return {"code": code, "status": "skipped", "stock_id": row["id"], "reason": "已在跟踪列表"}
            return {"code": code, "status": "exists", "stock_id": row["id"]}
        execute_update(
            "UPDATE stocks SET is_active=1, updated_at=datetime('now') WHERE id=?",
            (row["id"],),
        )
        return {"code": code, "status": "reactivated", "stock_id": row["id"]}

    stock_id = execute_insert(
        "INSERT INTO stocks (code, name, market) VALUES (?, ?, ?)",
        (code, code, market),
    )
    return {"code": code, "status": "added", "stock_id": stock_id}


def register_stocks(
    codes: list[str],
    market: str = "A",
    skip_existing: bool = True,
) -> list[dict]:
    out = []
    for code in codes:
        c = str(code).strip()
        if not c:
            continue
        out.append(register_stock(c, market, skip_existing=skip_existing))
    return out


def _prefetch_quotes(stock_rows: list[dict]) -> int:
    if not stock_rows:
        return 0
    try:
        from services.data_sources import tencent_quote

        codes = [s["code"] for s in stock_rows]
        quotes = tencent_quote(codes)
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        n = 0
        for s in stock_rows:
            q = quotes.get(s["code"], {})
            if q.get("pe_ttm") is None:
                continue
            conn.execute(
                """INSERT OR REPLACE INTO valuation_snapshots
                   (stock_id, pe_ttm, pb, market_cap, dividend_yield, as_of_date)
                   VALUES (?, ?, ?, ?, ?, date('now'))""",
                (
                    s["id"],
                    q.get("pe_ttm"),
                    q.get("pb"),
                    q.get("mcap_yi"),
                    None,
                ),
            )
            n += 1
        conn.commit()
        conn.close()
        return n
    except Exception as e:
        print(f"[onboard] prefetch quotes failed: {e}")
        return 0


def _fetch_pool(
    stock_rows: list[dict],
    parallel: int,
    *,
    finance_fast: bool | None = None,
) -> dict[str, Any]:
    done = 0
    total = len(stock_rows)
    errors: list[dict] = []
    failed_codes: list[str] = []
    use_fast = config.FINANCE_FAST_PATH if finance_fast is None else finance_fast

    def _one(s: dict) -> tuple[int, str, str | None]:
        sid, code = s["id"], s["code"]
        market = s.get("market") or "A"
        try:
            fetch_job.start_job(sid)
            result = fetch_job.sync_fetch_one(
                sid, code, market, finance_fast=use_fast
            )
            fetch_job.complete_job(sid, result, auto_score=False)
            if result.get("status") == "error":
                return sid, code, "fetch error"
            return sid, code, None
        except Exception as e:
            fetch_job.fail_job(sid, str(e))
            return sid, code, str(e)

    workers = max(1, min(parallel, total or 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_one, s): s for s in stock_rows}
        for fut in as_completed(futures):
            done += 1
            sid, code, err = fut.result()
            if err:
                failed_codes.append(code)
                errors.append({"stock_id": sid, "code": code, "message": err})

    return {
        "done": done,
        "total": total,
        "failed_codes": failed_codes,
        "errors": errors,
    }


def _run_factor(stock_ids: list[int]) -> dict:
    if not stock_ids:
        return {"ok": True, "count": 0}
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        from services.factor_engine import FactorEngine

        FactorEngine(conn).calculate_all(stock_ids)
        conn.close()
        return {"ok": True, "count": len(stock_ids)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _enqueue_score(stock_ids: list[int], score_mode: str) -> dict:
    if not stock_ids:
        return {"skipped": True, "reason": "no stock_ids"}
    try:
        from services.job_queue import can_enqueue_batch_fill, enqueue_batch_fill

        ok, reason, running_id = can_enqueue_batch_fill()
        if not ok:
            return {"skipped": True, "reason": reason, "running_job_id": running_id}
        job = enqueue_batch_fill(
            {
                "mode": score_mode,
                "stock_ids": stock_ids,
                "skip_no_source": True,
                "triggered_by": "onboard",
            }
        )
        return {"skipped": False, "batch_fill_job_id": job.id}
    except Exception as e:
        return {"skipped": True, "reason": str(e)}


def run_onboard_job(payload: dict) -> dict:
    market = payload.get("market") or "A"
    skip_existing = payload.get("skip_existing", True)
    auto_score = payload.get("auto_score", config.AUTO_SCORE_ON_FETCH)
    score_mode = payload.get("score_mode") or config.ONBOARD_SCORE_MODE
    parallel = int(payload.get("fetch_parallel") or config.FETCH_PARALLEL)

    progress: dict[str, Any] = {
        "phase": "register",
        "done": 0,
        "total": 0,
        "message": "",
        "errors": [],
    }

    # P1 register
    stock_ids: list[int] = list(payload.get("stock_ids") or [])
    registered: list[dict] = list(payload.get("registered") or [])

    if payload.get("codes") and not stock_ids:
        registered = register_stocks(payload["codes"], market, skip_existing=skip_existing)
        stock_ids = [r["stock_id"] for r in registered if r.get("stock_id")]

    if not stock_ids:
        progress["phase"] = "done"
        progress["message"] = "无有效股票"
        return {"ok": True, "registered": registered, "progress": progress, "stock_ids": []}

    rows = execute_sql(
        f"SELECT id, code, market FROM stocks WHERE id IN ({','.join('?' * len(stock_ids))}) AND is_active=1",
        tuple(stock_ids),
    )
    stock_rows = [dict(r) for r in rows]
    progress["total"] = len(stock_rows)

    # P2 prefetch
    progress["phase"] = "prefetch"
    progress["message"] = "预取行情"
    prefetch_n = _prefetch_quotes(stock_rows)
    progress["prefetch_quotes"] = prefetch_n

    # P3 fetch
    progress["phase"] = "fetch"
    progress["message"] = "并行抓取"
    fetch_result = _fetch_pool(
        stock_rows,
        parallel,
        finance_fast=payload.get("finance_fast"),
    )
    progress["done"] = fetch_result["done"]
    progress["errors"].extend(fetch_result["errors"])

    ok_ids = [
        s["id"]
        for s in stock_rows
        if s["code"] not in fetch_result["failed_codes"]
    ]

    # P4 factor（fetch 内已算 factor_scores；此处与 fetch-all 对齐再跑一遍批量）
    progress["phase"] = "factor"
    progress["message"] = "因子评分"
    progress["factor"] = _run_factor(ok_ids)

    # P5 score
    score_info: dict = {"skipped": True}
    if auto_score:
        progress["phase"] = "score"
        progress["message"] = "八维补算"
        score_info = _enqueue_score(ok_ids, score_mode)
        progress["score"] = score_info
    else:
        progress["phase"] = "done"
        progress["message"] = "跳过评分"

    progress["phase"] = "done"
    progress["message"] = f"完成 {len(ok_ids)}/{len(stock_rows)}"

    return {
        "ok": True,
        "registered": registered,
        "stock_ids": stock_ids,
        "ok_stock_ids": ok_ids,
        "progress": progress,
        "score": score_info,
        "fetch": fetch_result,
    }


def enqueue_onboard(payload: dict) -> Any:
    from services.job_queue import enqueue

    suffix = str(uuid.uuid4())[:6]
    job_id = f"ob-{suffix}"
    return enqueue(ONBOARD_JOB_TYPE, payload, job_id=job_id)
