"""财报披露日历 — 未来函数检测基础数据"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Optional

from config import DB_PATH

CONSERVATIVE_LAG_DAYS = 45

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS financial_calendar (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id INTEGER NOT NULL,
    period_end_date TEXT NOT NULL,
    report_type TEXT DEFAULT 'annual',
    disclosure_date TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'conservative+45',
    UNIQUE(stock_id, period_end_date, report_type)
);
CREATE INDEX IF NOT EXISTS idx_fin_cal_stock ON financial_calendar(stock_id, period_end_date);
CREATE INDEX IF NOT EXISTS idx_fin_cal_disc ON financial_calendar(stock_id, disclosure_date);
"""


def ensure_tables(conn: Optional[sqlite3.Connection] = None) -> None:
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    conn.executescript(CREATE_SQL)
    conn.commit()
    if own:
        conn.close()


def _conservative_disclosure(period_end: str) -> str:
    d = date.fromisoformat(period_end[:10])
    return (d + timedelta(days=CONSERVATIVE_LAG_DAYS)).isoformat()


def _report_type_for_period_end(period_end: date) -> str:
    if period_end.month == 3:
        return "q1"
    if period_end.month == 6:
        return "q2"
    if period_end.month == 9:
        return "q3"
    return "annual"


def _next_period_end(latest: date) -> tuple[date, str]:
    """按 A 股报告期顺序返回下一期 (period_end, report_type)。"""
    if latest.month == 3:
        return date(latest.year, 6, 30), "q2"
    if latest.month == 6:
        return date(latest.year, 9, 30), "q3"
    if latest.month == 9:
        return date(latest.year, 12, 31), "annual"
    return date(latest.year + 1, 3, 31), "q1"


def _statutory_disclosure_deadline(period_end: date) -> date:
    """法定披露截止日（窗口末），用于 upcoming 估算。"""
    y = period_end.year
    if period_end.month == 3:
        return date(y, 4, 30)
    if period_end.month == 6:
        return date(y, 8, 31)
    if period_end.month == 9:
        return date(y, 10, 31)
    return date(y + 1, 4, 30)


def project_upcoming_disclosures(
    conn: Optional[sqlite3.Connection] = None,
    *,
    ahead_days: int = 365,
) -> dict:
    """
    基于每只股票最新 report 期，推算未来尚未入库的披露节点。
    source=projected_statutory；不覆盖已有 report_date 精确披露。
    """
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    ensure_tables(conn)

    today = date.today()
    horizon = today + timedelta(days=ahead_days)
    written = 0

    stock_rows = conn.execute(
        """SELECT s.id,
                  COALESCE(
                    (SELECT MAX(fr.period_end_date) FROM financial_reports fr WHERE fr.stock_id = s.id),
                    (SELECT MAX(fc.period_end_date) FROM financial_calendar fc WHERE fc.stock_id = s.id)
                  ) AS latest_period
           FROM stocks s
           WHERE s.is_active = 1"""
    ).fetchall()

    for stock_id, latest_period in stock_rows:
        if not latest_period:
            continue
        cur_end = date.fromisoformat(str(latest_period)[:10])
        cur_type = _report_type_for_period_end(cur_end)

        # 至少推下一期；若仍在未来窗口内则继续
        for _ in range(4):
            cur_end, cur_type = _next_period_end(cur_end)
            disc = _statutory_disclosure_deadline(cur_end)
            if disc < today:
                continue
            if disc > horizon:
                break

            existing = conn.execute(
                """SELECT source FROM financial_calendar
                   WHERE stock_id=? AND period_end_date=? AND report_type=?""",
                (stock_id, cur_end.isoformat(), cur_type),
            ).fetchone()
            if existing and existing[0] == "report_date":
                continue

            conn.execute(
                """INSERT INTO financial_calendar
                   (stock_id, period_end_date, report_type, disclosure_date, source)
                   VALUES (?, ?, ?, ?, 'projected_statutory')
                   ON CONFLICT(stock_id, period_end_date, report_type) DO UPDATE SET
                     disclosure_date=excluded.disclosure_date,
                     source=CASE
                       WHEN financial_calendar.source='report_date' THEN financial_calendar.source
                       ELSE excluded.source
                     END""",
                (stock_id, cur_end.isoformat(), cur_type, disc.isoformat()),
            )
            written += 1

    if own:
        conn.commit()
        conn.close()
    return {"projected": written}


def sync_financial_calendar(
    conn: Optional[sqlite3.Connection] = None,
    *,
    ahead_days: int = 365,
) -> dict:
    """历史披露 rebuild + 未来节点 projection。"""
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    hist = rebuild_from_financial_reports(conn)
    proj = project_upcoming_disclosures(conn, ahead_days=ahead_days)
    if own:
        conn.commit()
        conn.close()
    return {"historical": hist, "projected": proj}


def ensure_calendar_ready(
    conn: sqlite3.Connection,
    *,
    ahead_days: int = 365,
) -> None:
    """若未来窗口无节点则自动 sync（首次打开日历页）。"""
    upcoming = conn.execute(
        "SELECT COUNT(*) FROM financial_calendar WHERE disclosure_date >= date('now')"
    ).fetchone()[0]
    if upcoming == 0:
        sync_financial_calendar(conn, ahead_days=ahead_days)
        conn.commit()


def rebuild_from_financial_reports(conn: Optional[sqlite3.Connection] = None) -> dict:
    """从 financial_reports 构建披露日；无 report_date 时用 period_end+45。"""
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    ensure_tables(conn)

    if not conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='financial_reports'"
    ).fetchone():
        if own:
            conn.close()
        return {"rows": 0, "skipped": True, "reason": "no financial_reports table"}

    rows = conn.execute(
        """SELECT stock_id, period_end_date, report_type, report_date
           FROM financial_reports
           WHERE period_end_date IS NOT NULL"""
    ).fetchall()

    written = 0
    for stock_id, period_end, report_type, report_date in rows:
        if report_date:
            disc = str(report_date)[:10]
            source = "report_date"
        else:
            disc = _conservative_disclosure(str(period_end)[:10])
            source = "conservative+45"
        conn.execute(
            """INSERT INTO financial_calendar
               (stock_id, period_end_date, report_type, disclosure_date, source)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(stock_id, period_end_date, report_type) DO UPDATE SET
                 disclosure_date=excluded.disclosure_date,
                 source=excluded.source""",
            (stock_id, str(period_end)[:10], report_type or "annual", disc, source),
        )
        written += 1

    if own:
        conn.commit()
        conn.close()
    return {"rows": written}


def earliest_valid_fundamental_date(stock_id: int, conn: Optional[sqlite3.Connection] = None) -> Optional[str]:
    """该股票最早可用的基本面因子 calc_date（任一披露日）。"""
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    ensure_tables(conn)
    row = conn.execute(
        """SELECT MIN(disclosure_date) FROM financial_calendar WHERE stock_id=?""",
        (stock_id,),
    ).fetchone()
    if own:
        conn.close()
    return row[0] if row and row[0] else None


def is_fundamental_valid_at(stock_id: int, calc_date: str, conn: Optional[sqlite3.Connection] = None) -> bool:
    """
    calc_date 当日基本面分是否无未来函数：
    取 period_end <= calc_date 的最新财报，其 disclosure_date 须 <= calc_date。
    无 calendar 数据时不剔除（返回 True，由 quality_flags 标记 unknown）。
    """
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    ensure_tables(conn)
    row = conn.execute(
        """SELECT disclosure_date FROM financial_calendar
           WHERE stock_id=? AND period_end_date <= ?
           ORDER BY period_end_date DESC LIMIT 1""",
        (stock_id, calc_date),
    ).fetchone()
    if own:
        conn.close()
    if not row:
        return True
    return str(row[0])[:10] <= calc_date[:10]
