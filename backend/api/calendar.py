"""
财报日历 API (PC-1.0 P1-1)

数据源：financial_calendar 表（已有）
额外标注：disclosure_date 前后3天内有 score_change_log 记录则标 near_alert=True
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from fastapi import APIRouter, Query
import config
from services.financial_calendar import ensure_calendar_ready

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


def _db():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


REPORT_TYPE_LABEL = {
    "annual": "年报",
    "semi": "半年报",
    "q1": "一季报",
    "q2": "半年报",
    "q3": "三季报",
    "q3_quarterly": "三季报",
}


@router.get("/events")
def get_calendar_events(
    days_ahead: int = Query(90, ge=1, le=365, description="未来 N 天"),
    days_behind: int = Query(30, ge=0, le=180, description="过去 N 天"),
    stock_ids: str = Query("", description="逗号分隔的 stock_id，为空返回全部"),
):
    """
    返回 [today-days_behind, today+days_ahead] 区间内的财报披露日历。
    附带：是否在披露日附近发生过分数变动（near_alert）。
    """
    today = date.today()
    start = (today - timedelta(days=days_behind)).isoformat()
    end = (today + timedelta(days=days_ahead)).isoformat()

    conn = _db()
    try:
        ensure_calendar_ready(conn, ahead_days=max(days_ahead, 90))
        sid_filter = ""
        params: list = [start, end]
        if stock_ids.strip():
            ids = [int(x) for x in stock_ids.split(",") if x.strip().isdigit()]
            if ids:
                ph = ",".join("?" * len(ids))
                sid_filter = f"AND fc.stock_id IN ({ph})"
                params.extend(ids)

        rows = conn.execute(
            f"""
            SELECT fc.stock_id, s.code, s.name, s.industry_sw,
                   fc.period_end_date, fc.report_type, fc.disclosure_date, fc.source
            FROM financial_calendar fc
            JOIN stocks s ON s.id = fc.stock_id
            WHERE fc.disclosure_date BETWEEN ? AND ?
              {sid_filter}
            ORDER BY fc.disclosure_date ASC, s.code ASC
            """,
            params,
        ).fetchall()

        # 标注附近有分数变动的条目（score_change_log 可能尚未迁移）
        alert_dates: set[tuple[int, str]] = set()
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='score_change_log'"
        ).fetchone():
            alert_rows = conn.execute(
                """
                SELECT stock_id, calc_date FROM score_change_log
                WHERE created_at >= date('now', '-180 days')
                """
            ).fetchall()
            for ar in alert_rows:
                alert_dates.add((ar["stock_id"], ar["calc_date"]))

    finally:
        conn.close()

    events = []
    for r in rows:
        disc = r["disclosure_date"]
        sid = r["stock_id"]
        # 检查披露日 ±3 天内是否有分数变动
        near_alert = any(
            (sid, (date.fromisoformat(disc) + timedelta(days=d)).isoformat()) in alert_dates
            for d in range(-3, 4)
        )
        events.append({
            "stock_id": sid,
            "code": r["code"],
            "name": r["name"],
            "industry": r["industry_sw"],
            "period_end_date": r["period_end_date"],
            "report_type": r["report_type"],
            "report_label": REPORT_TYPE_LABEL.get(r["report_type"], r["report_type"]),
            "disclosure_date": disc,
            "source": r["source"],
            "is_today": disc == today.isoformat(),
            "is_past": disc < today.isoformat(),
            "days_until": (date.fromisoformat(disc) - today).days,
            "near_alert": near_alert,
        })

    return {
        "today": today.isoformat(),
        "range": {"start": start, "end": end},
        "count": len(events),
        "events": events,
    }


@router.get("/upcoming")
def get_upcoming(limit: int = Query(10, ge=1, le=50)):
    """Dashboard 小组件：最近 N 条即将披露的财报。"""
    today = date.today().isoformat()
    conn = _db()
    try:
        ensure_calendar_ready(conn, ahead_days=180)
        rows = conn.execute(
            """
            SELECT fc.stock_id, s.code, s.name, fc.report_type, fc.disclosure_date
            FROM financial_calendar fc
            JOIN stocks s ON s.id = fc.stock_id
            WHERE fc.disclosure_date >= ?
            ORDER BY fc.disclosure_date ASC LIMIT ?
            """,
            (today, limit),
        ).fetchall()
    finally:
        conn.close()
    return {
        "events": [
            {
                "stock_id": r["stock_id"],
                "code": r["code"],
                "name": r["name"],
                "report_label": REPORT_TYPE_LABEL.get(r["report_type"], r["report_type"]),
                "disclosure_date": r["disclosure_date"],
                "days_until": (date.fromisoformat(r["disclosure_date"]) - date.today()).days,
            }
            for r in rows
        ]
    }
