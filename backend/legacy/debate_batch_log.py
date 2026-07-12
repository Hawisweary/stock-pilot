"""debate_batch_log 审计日志"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

import config

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS debate_batch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    job_id TEXT,
    mode TEXT,
    calc_date TEXT,
    target_date TEXT,
    total INTEGER,
    to_run INTEGER,
    llm_count INTEGER,
    light_count INTEGER,
    skipped INTEGER,
    completed INTEGER,
    error_count INTEGER,
    batch_retry_passes INTEGER DEFAULT 0,
    concurrency INTEGER,
    tier_counts_json TEXT,
    skip_reasons_json TEXT,
    duration_ms INTEGER,
    triggered_by TEXT DEFAULT 'api',
    detail_json TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_debate_batch_log_job ON debate_batch_log(job_id, created_at);
CREATE INDEX IF NOT EXISTS idx_debate_batch_log_date ON debate_batch_log(target_date, created_at);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=120)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_table(conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    if own:
        conn = _connect()
    try:
        conn.executescript(_CREATE_SQL)
        conn.commit()
    finally:
        if own:
            conn.close()


def _insert(**fields: Any) -> int:
    ensure_table()
    cols = [k for k, v in fields.items() if v is not None]
    vals = [fields[k] for k in cols]
    conn = _connect()
    try:
        cur = conn.execute(
            f"INSERT INTO debate_batch_log ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})",
            vals,
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def log_batch_start(
    *,
    job_id: str | None,
    mode: str,
    plan: dict[str, Any],
    triggered_by: str = "api",
) -> int:
    return _insert(
        event_type="start",
        job_id=job_id,
        mode=mode,
        calc_date=plan.get("calc_date"),
        target_date=plan.get("today"),
        total=plan.get("total"),
        to_run=plan.get("to_run"),
        llm_count=plan.get("llm_count"),
        light_count=plan.get("light_count"),
        skipped=plan.get("skipped"),
        concurrency=plan.get("concurrency"),
        tier_counts_json=json.dumps(plan.get("tier_counts") or {}, ensure_ascii=False),
        skip_reasons_json=json.dumps(plan.get("skip_reasons") or {}, ensure_ascii=False),
        triggered_by=triggered_by,
    )


def log_batch_done(
    *,
    job_id: str | None,
    mode: str,
    plan: dict[str, Any],
    result: dict[str, Any],
    triggered_by: str = "api",
) -> int:
    errors = result.get("errors") or []
    return _insert(
        event_type="done",
        job_id=job_id,
        mode=mode,
        calc_date=plan.get("calc_date"),
        target_date=plan.get("today"),
        total=plan.get("total"),
        to_run=plan.get("to_run"),
        llm_count=plan.get("llm_count"),
        light_count=plan.get("light_count"),
        skipped=plan.get("skipped"),
        completed=result.get("completed"),
        error_count=len(errors),
        batch_retry_passes=result.get("batch_retry_passes"),
        concurrency=plan.get("concurrency"),
        tier_counts_json=json.dumps(plan.get("tier_counts") or {}, ensure_ascii=False),
        duration_ms=result.get("duration_ms"),
        triggered_by=triggered_by,
        detail_json=json.dumps(
            {
                "batch_retry_passes": result.get("batch_retry_passes"),
                "error_samples": errors[:5],
            },
            ensure_ascii=False,
        ),
    )


def query_debate_history(
    limit: int = 50,
    target_date: str | None = None,
    job_id: str | None = None,
    event_type: str | None = None,
) -> list[dict]:
    ensure_table()
    conn = _connect()
    try:
        clauses = ["1=1"]
        args: list[Any] = []
        if target_date:
            clauses.append("target_date=?")
            args.append(target_date)
        if job_id:
            clauses.append("job_id=?")
            args.append(job_id)
        if event_type:
            clauses.append("event_type=?")
            args.append(event_type)
        args.append(limit)
        sql = f"""
            SELECT * FROM debate_batch_log
            WHERE {' AND '.join(clauses)}
            ORDER BY created_at DESC LIMIT ?
        """
        rows = conn.execute(sql, tuple(args)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
