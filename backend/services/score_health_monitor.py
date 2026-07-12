"""必需维度 sync_rate 监控与告警"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Any

import config
from services.score_gap_log import (
    alert_cooled_down,
    log_alert,
    log_alert_resolved,
    log_scan,
)
from services.score_gap_scanner import REQUIRED_DIMENSIONS, scan_gaps

logger = logging.getLogger("afr.score_health")

SYNC_RATE_ALERT_THRESHOLD = float(os.getenv("AFR_SYNC_RATE_ALERT_THRESHOLD", "1.0"))
SYNC_RATE_ALERT_DURATION_MIN = int(os.getenv("AFR_SYNC_RATE_ALERT_DURATION_MIN", "30"))
SYNC_RATE_ALERT_COOLDOWN_MIN = int(os.getenv("AFR_SYNC_RATE_ALERT_COOLDOWN_MIN", "60"))

_low_since: dict[str, datetime] = {}
_alert_sent: set[str] = set()


def check_sync_rate(target_date: str | None = None) -> dict[str, Any]:
    report = scan_gaps(target_date=target_date)
    alert_key = f"{report['target_date']}:required_dims"
    return {
        **report,
        "sync_rate_all": report.get("sync_rate_all"),
        "sync_rate_required": report.get("sync_rate_required"),
        "missing_by_dimension": {
            dim: report.get("summary", {}).get(dim, {})
            for dim in REQUIRED_DIMENSIONS
        },
        "alert_eligible": report.get("sync_rate_required", 1.0) < SYNC_RATE_ALERT_THRESHOLD,
        "alert_key": alert_key,
    }


def _send_notification(title: str, body: str) -> list[str]:
    channels: list[str] = []
    webhook = os.getenv("AFR_ALERT_DINGTALK_WEBHOOK", "")
    if webhook:
        try:
            import urllib.request

            payload = {"msgtype": "text", "text": {"content": f"{title}\n{body}"}}
            import json

            req = urllib.request.Request(
                webhook,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=5)
            channels.append("dingtalk")
        except Exception as e:
            logger.warning("dingtalk alert failed: %s", e)
    email_to = os.getenv("AFR_ALERT_EMAIL_TO", "")
    if email_to:
        logger.info("[Alert email stub] to=%s %s", email_to, title)
        channels.append("email_stub")
    return channels


def maybe_send_alert(report: dict[str, Any]) -> None:
    if not report.get("alert_eligible"):
        _low_since.pop(report.get("alert_key", ""), None)
        return

    key = report["alert_key"]
    now = datetime.now()
    if key not in _low_since:
        _low_since[key] = now
        return

    elapsed_min = (now - _low_since[key]).total_seconds() / 60
    if elapsed_min < SYNC_RATE_ALERT_DURATION_MIN:
        return
    if alert_cooled_down(key, SYNC_RATE_ALERT_COOLDOWN_MIN):
        return
    if key in _alert_sent:
        return

    missing = report.get("missing_by_dimension", {})
    detail = {
        "sync_rate_required": report.get("sync_rate_required"),
        "active_stocks_count": report.get("active_stocks_count"),
        "duration_min": round(elapsed_min, 1),
        "missing_by_dimension": missing,
    }
    title = "【AFR 评分告警】"
    body = (
        f"{report['target_date']} 必需维度同步率 "
        f"{report.get('sync_rate_required', 0):.0%}（{report.get('active_stocks_count', 0)}股），"
        f"已持续 {elapsed_min:.0f} 分钟。"
    )
    channels = _send_notification(title, body)
    detail["channels"] = channels
    log_alert(key, detail, target_date=report["target_date"])
    _alert_sent.add(key)


def maybe_send_recovery(report: dict[str, Any], *, job_id: str | None = None) -> None:
    if report.get("sync_rate_required", 0) < SYNC_RATE_ALERT_THRESHOLD:
        return
    key = f"{report['target_date']}:required_dims"
    if key not in _alert_sent and key not in _low_since:
        return

    title = "【AFR 评分恢复】"
    body = (
        f"{report['target_date']} 必需维度同步率已恢复至 "
        f"{report.get('sync_rate_required', 0):.0%}（{report.get('active_stocks_count', 0)}/"
        f"{report.get('active_stocks_count', 0)}）"
    )
    if job_id:
        body += f"，补算 job {job_id} 已完成。"
    _send_notification(title, body)
    log_alert_resolved(key, target_date=report["target_date"], sync_rate_required_after=report["sync_rate_required"])
    _alert_sent.discard(key)
    _low_since.pop(key, None)


def run_monitor_cycle(target_date: str | None = None) -> dict[str, Any]:
    from services.batch_score_guard import can_run_sync

    if not can_run_sync()[0]:
        return {"skipped": True, "reason": "batch-fill active"}
    report = check_sync_rate(target_date=target_date)
    try:
        log_scan(report, triggered_by="monitor")
    except Exception as e:
        logger.warning("monitor log_scan failed: %s", e)
    maybe_send_alert(report)
    return report


def start_monitor_daemon(app, interval_sec: int = 300) -> None:
    import threading

    def _loop() -> None:
        while True:
            try:
                run_monitor_cycle()
            except Exception as e:
                logger.warning("monitor cycle failed: %s", e)
            threading.Event().wait(interval_sec)

    t = threading.Thread(target=_loop, daemon=True, name="score-health-monitor")
    t.start()
    logger.info("[ScoreHealthMonitor] started interval=%ss", interval_sec)
