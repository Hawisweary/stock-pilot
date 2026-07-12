"""
单股/批量抓取任务状态管理（内存缓存 + SQLite 持久化）
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from typing import Any

import config
from config import DB_PATH

_STALE_SECONDS = 20 * 60
_memory: dict[int, dict[str, Any]] = {}


def _connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=30000")
    db.execute("PRAGMA synchronous=NORMAL")
    return db


def sync_fetch_one(
    stock_id: int,
    code: str,
    market: str,
    *,
    finance_fast: bool | None = None,
    plan=None,
) -> dict:
    from services.data_fetcher import DataFetcher
    from services.fetch_planner import StockFetchPlan

    batch_commit = bool(plan and isinstance(plan, StockFetchPlan) and plan.batch_commit)
    db = _connect()
    try:
        result = DataFetcher(db, batch_commit=batch_commit).fetch_all_for_stock(
            stock_id,
            code,
            market or "A",
            finance_fast=finance_fast,
            plan=plan,
        )
        if not batch_commit:
            db.commit()
        return result
    finally:
        db.close()


def _persist(stock_id: int, job: dict[str, Any]) -> None:
    _memory[stock_id] = job
    payload = (
        stock_id,
        job.get("status", "pending"),
        1 if job.get("running") else 0,
        int(job.get("quotes", 0) or 0),
        int(job.get("financials", 0) or 0),
        int(job.get("indicators", 0) or 0),
        json.dumps(job.get("errors", []), ensure_ascii=False),
        job.get("error", "") or "",
    )
    sql = """
        INSERT INTO fetch_jobs (
            stock_id, status, running, quotes, financials, indicators,
            errors_json, error, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(stock_id) DO UPDATE SET
            status=excluded.status,
            running=excluded.running,
            quotes=excluded.quotes,
            financials=excluded.financials,
            indicators=excluded.indicators,
            errors_json=excluded.errors_json,
            error=excluded.error,
            updated_at=datetime('now')
    """
    for attempt in range(4):
        db = _connect()
        try:
            db.execute(sql, payload)
            db.commit()
            return
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower() or attempt >= 3:
                raise
            time.sleep(0.15 * (attempt + 1))
        finally:
            db.close()


def _load_db(stock_id: int) -> dict[str, Any] | None:
    db = _connect()
    try:
        row = db.execute(
            "SELECT * FROM fetch_jobs WHERE stock_id=?", (stock_id,)
        ).fetchone()
        if not row:
            return None
        errors = []
        try:
            errors = json.loads(row["errors_json"] or "[]")
        except json.JSONDecodeError:
            pass
        return {
            "stock_id": stock_id,
            "running": bool(row["running"]),
            "status": row["status"],
            "quotes": row["quotes"],
            "financials": row["financials"],
            "indicators": row["indicators"],
            "errors": errors,
            "error": row["error"] or "",
        }
    finally:
        db.close()


def get_job(stock_id: int) -> dict[str, Any] | None:
    if stock_id in _memory:
        return _memory[stock_id]
    job = _load_db(stock_id)
    if job:
        _memory[stock_id] = job
    return job


def status_payload(stock_id: int) -> dict[str, Any]:
    job = get_job(stock_id)
    if not job:
        return {"ok": True, "status": "not_started", "stock_id": stock_id, "running": False}
    return {
        "ok": True,
        "stock_id": stock_id,
        "running": bool(job.get("running")),
        "status": job.get("status", "pending"),
        "quotes": job.get("quotes", 0),
        "financials": job.get("financials", 0),
        "indicators": job.get("indicators", 0),
        "errors": job.get("errors", []),
        "error": job.get("error", ""),
        "batch_fill_job_id": job.get("batch_fill_job_id"),
    }


def is_running(stock_id: int) -> bool:
    job = get_job(stock_id)
    return bool(job and job.get("running"))


def start_job(stock_id: int) -> None:
    _persist(
        stock_id,
        {
            "running": True,
            "status": "pending",
            "stock_id": stock_id,
            "quotes": 0,
            "financials": 0,
            "indicators": 0,
            "errors": [],
            "error": "",
        },
    )


def complete_job(
    stock_id: int,
    result: dict,
    *,
    auto_score: bool | None = None,
    sync_gaps: bool = True,
) -> None:
    status = result.get("status", "success")
    errors = result.get("errors", [])
    err_msg = ""
    if status == "error" and errors:
        err_msg = "; ".join(
            f"{e.get('step', '?')}: {e.get('message', '')}" for e in errors
        )
    batch_fill_job_id: str | None = None
    if status in ("success", "partial"):
        if sync_gaps:
            _maybe_sync_gaps_after_fetch(stock_id)
        # Path A — inline V5 重算（单股；用户等待响应）
        if config.V5_RECALC_INLINE_ON_SINGLE_FETCH:
            _inline_v5(stock_id)
        if auto_score is None:
            auto_score = config.AUTO_SCORE_ON_FETCH
        if auto_score:
            batch_fill_job_id = _maybe_auto_batch_fill([stock_id])
    _persist(
        stock_id,
        {
            "running": False,
            "status": status,
            "stock_id": stock_id,
            "quotes": result.get("quotes_count", 0),
            "financials": result.get("financials_count", 0),
            "indicators": result.get("indicators_count", 0),
            "errors": errors,
            "error": err_msg,
            "batch_fill_job_id": batch_fill_job_id,
        },
    )


def _inline_v5(stock_id: int) -> None:
    """Path A: 单股 fetch 后 inline 触发 V5 重算。"""
    try:
        from services.v5_scorer import get_stock_v5_score
        get_stock_v5_score(stock_id)
        print(f"[fetch_job] Path A V5 inline done sid={stock_id}")
    except Exception as e:
        print(f"[fetch_job] Path A V5 inline failed sid={stock_id}: {e}")


def _maybe_auto_batch_fill(stock_ids: list[int]) -> str | None:
    if not stock_ids:
        return None
    try:
        from services.job_queue import can_enqueue_batch_fill, enqueue_batch_fill

        ok, reason, _ = can_enqueue_batch_fill()
        if not ok:
            print(f"[fetch_job] auto batch-fill skipped: {reason}")
            return None
        job = enqueue_batch_fill(
            {
                "mode": config.ONBOARD_SCORE_MODE,
                "stock_ids": stock_ids,
                "skip_no_source": True,
                "triggered_by": "fetch_auto",
            }
        )
        print(f"[fetch_job] auto batch-fill queued job={job.id} stocks={stock_ids}")
        return job.id
    except Exception as e:
        print(f"[fetch_job] auto batch-fill failed: {e}")
        return None


def _maybe_sync_gaps_after_fetch(stock_id: int) -> None:
    try:
        from services.batch_score_maintenance import can_run_sync, sync_gaps_after_fetch

        ok, _ = can_run_sync()
        if ok:
            sync_gaps_after_fetch()
    except Exception as e:
        print(f"[fetch_job] gap sync after sid={stock_id}: {e}")


def fail_job(stock_id: int, error: str) -> None:
    _persist(
        stock_id,
        {
            "running": False,
            "status": "error",
            "stock_id": stock_id,
            "quotes": 0,
            "financials": 0,
            "indicators": 0,
            "errors": [{"step": "fetch", "message": error}],
            "error": error,
        },
    )


def reset_stale_jobs() -> int:
    """启动时或查询前：将超时 running 任务标为 stale"""
    db = _connect()
    n = 0
    try:
        rows = db.execute(
            """
            SELECT stock_id FROM fetch_jobs
            WHERE running=1
              AND updated_at < datetime('now', ?)
            """,
            (f"-{_STALE_SECONDS} seconds",),
        ).fetchall()
        for row in rows:
            sid = row["stock_id"]
            fail_job(sid, "任务超时已重置")
            n += 1
    finally:
        db.close()
    return n


async def run_single_fetch_async(
    stock_id: int,
    code: str,
    market: str,
    *,
    finance_fast: bool | None = None,
) -> None:
    try:
        result = await asyncio.to_thread(
            sync_fetch_one, stock_id, code, market, finance_fast=finance_fast
        )
        complete_job(stock_id, result)
    except Exception as e:
        fail_job(stock_id, str(e))


def scheduler_fetch_all(
    stocks: list[dict],
    sleep_sec: float = 1.5,
    *,
    mode: str = "full",
) -> dict:
    """调度器批量抓取（与 API 共用 plan / 任务状态，夜间默认 full）"""
    import sqlite3
    import time

    from services.fetch_planner import build_plans

    failed: list[str] = []
    planner_conn = _connect()
    try:
        plans = build_plans(planner_conn, stocks, mode)
    finally:
        planner_conn.close()

    for s in stocks:
        sid, code = s["id"], s["code"]
        market = s.get("market") or "A"
        plan = plans.get(sid)
        try:
            start_job(sid)
            result = sync_fetch_one(
                sid,
                code,
                market,
                finance_fast=plan.finance_fast if plan else False,
                plan=plan,
            )
            complete_job(sid, result, auto_score=False)
            if result.get("status") == "error":
                failed.append(code)
        except Exception as e:
            fail_job(sid, str(e))
            failed.append(code)
        time.sleep(sleep_sec)
    return {"failed": failed, "total": len(stocks), "mode": mode}
