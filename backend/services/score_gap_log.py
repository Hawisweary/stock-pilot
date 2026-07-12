"""score_gap_log 审计日志"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

import config

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS score_gap_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    target_date TEXT NOT NULL,
    alert_key TEXT,
    mode TEXT,
    job_id TEXT,
    active_stocks_count INTEGER,
    stock_scope_json TEXT,
    sync_rate_all_before REAL,
    sync_rate_required_before REAL,
    sync_rate_all_after REAL,
    sync_rate_required_after REAL,
    gap_summary_json TEXT,
    actions_json TEXT,
    alert_detail_json TEXT,
    filled_count INTEGER DEFAULT 0,
    skipped_count INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    duration_ms INTEGER,
    triggered_by TEXT DEFAULT 'api',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_gap_log_date ON score_gap_log(target_date, created_at);
CREATE INDEX IF NOT EXISTS idx_gap_log_alert ON score_gap_log(alert_key, event_type, created_at);
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
            f"INSERT INTO score_gap_log ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})",
            vals,
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def log_scan(report: dict, *, triggered_by: str = "api") -> int:
    return _insert(
        event_type="scan",
        target_date=report.get("target_date"),
        active_stocks_count=report.get("active_stocks_count"),
        sync_rate_all_before=report.get("sync_rate_all"),
        sync_rate_required_before=report.get("sync_rate_required"),
        gap_summary_json=json.dumps(report.get("summary", {}), ensure_ascii=False),
        triggered_by=triggered_by,
    )


def log_fill_start(
    job_id: str,
    *,
    mode: str,
    target_date: str,
    before: dict,
    stock_ids: list[int] | None = None,
    triggered_by: str = "api",
) -> int:
    scope = json.dumps(stock_ids if stock_ids else "all_active", ensure_ascii=False)
    return _insert(
        event_type="fill_start",
        target_date=target_date,
        mode=mode,
        job_id=job_id,
        active_stocks_count=before.get("active_stocks_count"),
        stock_scope_json=scope,
        sync_rate_all_before=before.get("sync_rate_all"),
        sync_rate_required_before=before.get("sync_rate_required"),
        gap_summary_json=json.dumps(before.get("summary", {}), ensure_ascii=False),
        triggered_by=triggered_by,
    )


def log_fill_done(
    job_id: str,
    *,
    mode: str,
    target_date: str,
    before: dict,
    after: dict,
    actions: dict | None = None,
    duration_ms: int | None = None,
    triggered_by: str = "api",
) -> int:
    filled = sum(after.get("summary", {}).get(d, {}).get("ok", 0) for d in after.get("summary", {}))
    skipped = after.get("missing_total", 0)
    return _insert(
        event_type="fill_done",
        target_date=target_date,
        mode=mode,
        job_id=job_id,
        active_stocks_count=after.get("active_stocks_count"),
        sync_rate_all_before=before.get("sync_rate_all"),
        sync_rate_required_before=before.get("sync_rate_required"),
        sync_rate_all_after=after.get("sync_rate_all"),
        sync_rate_required_after=after.get("sync_rate_required"),
        gap_summary_json=json.dumps(after.get("summary", {}), ensure_ascii=False),
        actions_json=json.dumps(actions or {}, ensure_ascii=False),
        filled_count=filled,
        skipped_count=skipped,
        duration_ms=duration_ms,
        triggered_by=triggered_by,
    )


def log_fill_error(
    job_id: str,
    *,
    target_date: str,
    mode: str,
    error: str,
    triggered_by: str = "api",
) -> int:
    return _insert(
        event_type="fill_error",
        target_date=target_date,
        mode=mode,
        job_id=job_id,
        actions_json=json.dumps({"error": error}, ensure_ascii=False),
        error_count=1,
        triggered_by=triggered_by,
    )


def log_alert(alert_key: str, detail: dict, *, target_date: str) -> int:
    return _insert(
        event_type="alert",
        target_date=target_date,
        alert_key=alert_key,
        alert_detail_json=json.dumps(detail, ensure_ascii=False),
        sync_rate_required_before=detail.get("sync_rate_required"),
        active_stocks_count=detail.get("active_stocks_count"),
        triggered_by="monitor",
    )


def log_alert_resolved(alert_key: str, *, target_date: str, sync_rate_required_after: float) -> int:
    return _insert(
        event_type="alert_resolved",
        target_date=target_date,
        alert_key=alert_key,
        sync_rate_required_after=sync_rate_required_after,
        triggered_by="monitor",
    )


def alert_cooled_down(alert_key: str, cooldown_min: int = 60) -> bool:
    ensure_table()
    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT 1 FROM score_gap_log
            WHERE alert_key=? AND event_type='alert'
              AND datetime(created_at) > datetime('now', ?)
            LIMIT 1
            """,
            (alert_key, f"-{cooldown_min} minutes"),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def query_gap_history(limit: int = 50, target_date: str | None = None) -> list[dict]:
    ensure_table()
    conn = _connect()
    try:
        if target_date:
            rows = conn.execute(
                """
                SELECT * FROM score_gap_log
                WHERE target_date=?
                ORDER BY created_at DESC LIMIT ?
                """,
                (target_date, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM score_gap_log ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def sync_rate_trend(days: int = 7) -> list[dict]:
    """最近 N 天 scan / fill_done 的 sync_rate_required 趋势。"""
    ensure_table()
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT target_date, event_type, sync_rate_required_after, sync_rate_required_before,
                   created_at
            FROM score_gap_log
            WHERE event_type IN ('scan', 'fill_done')
              AND datetime(created_at) >= datetime('now', ?)
            ORDER BY created_at ASC
            """,
            (f"-{days} days",),
        ).fetchall()
        out: list[dict] = []
        for r in rows:
            rate = r["sync_rate_required_after"]
            if rate is None:
                rate = r["sync_rate_required_before"]
            out.append(
                {
                    "target_date": r["target_date"],
                    "event_type": r["event_type"],
                    "sync_rate_required": rate,
                    "created_at": r["created_at"],
                }
            )
        return out
    finally:
        conn.close()


def sync_rate_trend_daily(days: int = 7) -> list[dict]:
    """按 target_date 聚合，每日取最新一条 sync_rate_required。"""
    ensure_table()
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT target_date, rate FROM (
                SELECT target_date,
                    COALESCE(sync_rate_required_after, sync_rate_required_before) AS rate,
                    ROW_NUMBER() OVER (
                        PARTITION BY target_date ORDER BY datetime(created_at) DESC
                    ) AS rn
                FROM score_gap_log
                WHERE event_type IN ('scan', 'fill_done')
                  AND datetime(created_at) >= datetime('now', ?)
            ) t
            WHERE rn = 1
            ORDER BY target_date ASC
            """,
            (f"-{days} days",),
        ).fetchall()
        return [
            {"date": r["target_date"], "sync_rate_required": r["rate"]}
            for r in rows
            if r["rate"] is not None
        ]
    finally:
        conn.close()


def cleanup_old_logs(
    *,
    retention_days: int | None = None,
    alert_retention_days: int | None = None,
) -> dict[str, int]:
    """清理过期 score_gap_log（Phase 3）。"""
    ensure_table()
    retain = retention_days if retention_days is not None else config.GAP_LOG_RETENTION_DAYS
    alert_retain = (
        alert_retention_days
        if alert_retention_days is not None
        else config.GAP_LOG_ALERT_RETENTION_DAYS
    )
    conn = _connect()
    try:
        cur1 = conn.execute(
            """
            DELETE FROM score_gap_log
            WHERE datetime(created_at) < datetime('now', ?)
              AND event_type NOT IN ('alert', 'alert_resolved')
            """,
            (f"-{retain} days",),
        )
        cur2 = conn.execute(
            """
            DELETE FROM score_gap_log
            WHERE event_type IN ('alert', 'alert_resolved')
              AND datetime(created_at) < datetime('now', ?)
            """,
            (f"-{alert_retain} days",),
        )
        conn.commit()
        return {
            "deleted_general": cur1.rowcount,
            "deleted_alerts": cur2.rowcount,
        }
    finally:
        conn.close()
