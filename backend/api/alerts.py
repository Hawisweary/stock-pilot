"""分数变动预警 API (PC-1.0 P0-2)"""
from __future__ import annotations

import sqlite3
from fastapi import APIRouter, Query
import config

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


def _db():
    return sqlite3.connect(config.DB_PATH)


@router.get("/score-changes")
def get_score_changes(
    days: int = Query(7, ge=1, le=90, description="最近 N 天"),
    min_delta: float = Query(5.0, ge=0),
    veto_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
):
    """
    返回最近 N 天内分数变动 >= min_delta 或 veto 变化的股票列表。
    前端 Dashboard 用于展示 Toast 预警卡片。
    """
    conn = _db()
    try:
        rows = conn.execute(
            """
            SELECT
                scl.id, scl.stock_id, s.code, s.name,
                scl.calc_date, scl.old_score, scl.new_score, scl.delta,
                scl.old_veto, scl.new_veto, scl.veto_changed, scl.created_at
            FROM score_change_log scl
            JOIN stocks s ON s.id = scl.stock_id
            WHERE scl.created_at >= datetime('now', ? || ' days')
              AND (ABS(scl.delta) >= ? OR (? AND scl.veto_changed = 1))
            ORDER BY ABS(scl.delta) DESC, scl.created_at DESC
            LIMIT ?
            """,
            (f"-{days}", min_delta, int(veto_only), limit),
        ).fetchall()
    finally:
        conn.close()

    cols = ["id", "stock_id", "code", "name", "calc_date",
            "old_score", "new_score", "delta",
            "old_veto", "new_veto", "veto_changed", "created_at"]
    return {"alerts": [dict(zip(cols, r)) for r in rows]}


@router.get("/score-changes/unread-count")
def get_unread_count(since_hours: int = Query(24, ge=1, le=168)):
    """Dashboard badge 用：返回最近 N 小时内的预警数量。"""
    conn = _db()
    try:
        count = conn.execute(
            """
            SELECT COUNT(*) FROM score_change_log
            WHERE created_at >= datetime('now', ? || ' hours')
            """,
            (f"-{since_hours}",),
        ).fetchone()[0]
    finally:
        conn.close()
    return {"count": count, "since_hours": since_hours}
