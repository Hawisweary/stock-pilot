"""Dashboard 评分同步健康聚合"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

import config
from services.score_gap_log import ensure_table, sync_rate_trend_daily
from services.score_gap_scanner import ALL_SYNC_DIMENSIONS, REQUIRED_DIMENSIONS, scan_gaps
from services.score_health_monitor import (
    SYNC_RATE_ALERT_DURATION_MIN,
    SYNC_RATE_ALERT_THRESHOLD,
    check_sync_rate,
)

BATCH_FILL_JOB_TYPE = "batch_score_fill"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _active_alert(alert_key: str, target_date: str, sync_rate_required: float) -> dict[str, Any]:
    alert: dict[str, Any] = {"active": False, "alert_key": alert_key}
    if sync_rate_required >= SYNC_RATE_ALERT_THRESHOLD:
        return alert

    ensure_table()
    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT created_at, alert_detail_json, sync_rate_required_before
            FROM score_gap_log
            WHERE alert_key=? AND event_type='alert' AND target_date=?
            ORDER BY created_at DESC LIMIT 1
            """,
            (alert_key, target_date),
        ).fetchone()
        if not row:
            return alert

        resolved = conn.execute(
            """
            SELECT 1 FROM score_gap_log
            WHERE alert_key=? AND event_type='alert_resolved'
              AND datetime(created_at) > datetime(?)
            LIMIT 1
            """,
            (alert_key, row["created_at"]),
        ).fetchone()
        if resolved:
            return alert

        detail = {}
        if row["alert_detail_json"]:
            try:
                detail = json.loads(row["alert_detail_json"])
            except json.JSONDecodeError:
                pass

        since = row["created_at"]
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", ""))
            duration_min = (datetime.now() - since_dt).total_seconds() / 60
        except ValueError:
            duration_min = detail.get("duration_min", 0)

        alert.update(
            {
                "active": duration_min >= SYNC_RATE_ALERT_DURATION_MIN,
                "since": since,
                "duration_minutes": round(duration_min, 1),
                "last_notified_at": since,
                "channels_sent": detail.get("channels", []),
                "sync_rate_required": row["sync_rate_required_before"],
            }
        )
        return alert
    finally:
        conn.close()


def _last_fill_job() -> dict[str, Any] | None:
    conn = _connect()
    try:
        try:
            row = conn.execute(
                """
                SELECT id, status, result_json, error, finished_at, created_at
                FROM job_runs
                WHERE job_type=?
                ORDER BY COALESCE(finished_at, created_at) DESC
                LIMIT 1
                """,
                (BATCH_FILL_JOB_TYPE,),
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        if not row:
            return None
        result = None
        if row["result_json"]:
            try:
                result = json.loads(row["result_json"])
            except json.JSONDecodeError:
                result = {"raw": row["result_json"]}
        after_rate = None
        if isinstance(result, dict):
            after = result.get("after") or {}
            after_rate = after.get("sync_rate_required")
        return {
            "job_id": row["id"],
            "status": row["status"],
            "error": row["error"],
            "finished_at": row["finished_at"],
            "sync_rate_required_after": after_rate,
        }
    finally:
        conn.close()


def _gaps_by_dimension(gaps: list[dict], summary: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for dim in ALL_SYNC_DIMENSIONS:
        dim_gaps = [g for g in gaps if g.get("dimension") == dim]
        out[dim] = {
            "ok": summary.get(dim, {}).get("ok", 0),
            "missing": summary.get(dim, {}).get("missing", 0),
            "no_source": summary.get(dim, {}).get("no_source", 0),
            "stale": summary.get(dim, {}).get("stale", 0),
            "required": dim in REQUIRED_DIMENSIONS,
            "stock_ids_missing": [
                int(g["stock_id"]) for g in dim_gaps if g.get("status") == "missing"
            ],
            "stock_ids_no_source": [
                int(g["stock_id"]) for g in dim_gaps if g.get("status") == "no_source"
            ],
        }
    return out


def get_score_sync_health(target_date: str | None = None) -> dict[str, Any]:
    report = scan_gaps(target_date=target_date)
    health = check_sync_rate(target_date=report["target_date"])
    alert_key = health["alert_key"]
    active = _active_alert(alert_key, report["target_date"], report["sync_rate_required"])

    stocks_full_required = int(round(report["sync_rate_required"] * report["active_stocks_count"]))
    missing_by_dimension = {
        dim: report["summary"].get(dim, {}).get("missing", 0) for dim in REQUIRED_DIMENSIONS
    }

    return {
        "target_date": report["target_date"],
        "active_stocks_count": report["active_stocks_count"],
        "sync_rate_all": report["sync_rate_all"],
        "sync_rate_required": report["sync_rate_required"],
        "stocks_full_required": stocks_full_required,
        "missing_total": report["missing_total"],
        "stale_total": report.get("stale_total", 0),
        "gap_stale_days": report.get("gap_stale_days", config.GAP_STALE_DAYS),
        "missing_by_dimension": missing_by_dimension,
        "gaps_by_dimension": _gaps_by_dimension(report.get("gaps", []), report.get("summary", {})),
        "alert": active,
        "alert_threshold": SYNC_RATE_ALERT_THRESHOLD,
        "last_fill_job": _last_fill_job(),
        "trend_7d": sync_rate_trend_daily(days=7),
        "recommended_actions": report.get("recommended_actions", []),
    }


def get_score_sync_trend(days: int = 7) -> dict[str, Any]:
    return {"days": days, "trend": sync_rate_trend_daily(days=days)}
