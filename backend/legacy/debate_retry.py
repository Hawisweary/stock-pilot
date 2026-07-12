"""辩论批量补跑 — 从 job 失败项收集并重试。"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

import config
from services.job_queue import DEBATE_BATCH_JOB_TYPE, get_job


def failures_from_result(result: dict | None) -> list[dict[str, Any]]:
    if not result:
        return []
    explicit = result.get("errors") or []
    if explicit:
        return explicit
    return [r for r in result.get("results") or [] if r.get("error")]


def _latest_debate_job_with_failures() -> tuple[str | None, dict | None]:
    try:
        conn = sqlite3.connect(config.DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, result_json FROM job_runs
            WHERE job_type=? AND status='done'
            ORDER BY finished_at DESC LIMIT 15
            """,
            (DEBATE_BATCH_JOB_TYPE,),
        ).fetchall()
        conn.close()
    except Exception:
        return None, None

    for row in rows:
        if not row["result_json"]:
            continue
        try:
            result = json.loads(row["result_json"])
        except json.JSONDecodeError:
            continue
        if failures_from_result(result):
            return row["id"], result
    return None, None


def resolve_retry_stock_ids(
    retry_job_id: str | None = None,
    *,
    stock_ids: list[int] | None = None,
) -> dict[str, Any]:
    """解析补跑目标：优先 retry_job_id，否则最近一条含失败的 done job。"""
    source_job_id = retry_job_id
    result: dict | None = None

    if retry_job_id:
        job = get_job(retry_job_id)
        if not job:
            return {
                "retry_job_id": retry_job_id,
                "source_job_id": retry_job_id,
                "stock_ids": [],
                "failures": [],
                "message": "job 不存在",
            }
        result = job.result
        source_job_id = job.id
    else:
        source_job_id, result = _latest_debate_job_with_failures()

    failures = failures_from_result(result)
    ids = sorted({int(f["stock_id"]) for f in failures if f.get("stock_id") is not None})

    if stock_ids:
        allowed = set(stock_ids)
        ids = [i for i in ids if i in allowed]
        failures = [f for f in failures if int(f.get("stock_id") or 0) in allowed]

    return {
        "retry_job_id": retry_job_id,
        "source_job_id": source_job_id,
        "stock_ids": ids,
        "failures": failures,
        "failure_count": len(failures),
        "message": None if ids else "无失败项可补跑",
    }
