"""V5 一票否决风险标记扫描。"""
from __future__ import annotations

import re
import sqlite3

import config
from config import latest_trading_date

ST_NAME_RE = re.compile(r"\*?ST", re.IGNORECASE)


def _is_st_name(name: str) -> bool:
    n = (name or "").strip()
    if not n:
        return False
    return bool(ST_NAME_RE.search(n))


def _limit_down_threshold(code: str, name: str = "") -> float:
    """创业板/科创板 20%，其余 10%（ST 5% 近似用 4.8）。"""
    c = str(code)
    if c.startswith(("300", "301", "688", "689")):
        return 19.5
    if _is_st_name(name):
        return 4.8
    return 9.5


def _scan_st_flags(conn: sqlite3.Connection, flag_date: str) -> int:
    rows = conn.execute(
        "SELECT id, name FROM stocks WHERE is_active=1"
    ).fetchall()
    n = 0
    for sid, name in rows:
        if not _is_st_name(name):
            continue
        conn.execute(
            """INSERT OR IGNORE INTO risk_flags
            (stock_id, flag_date, flag_type, severity, detail, source)
            VALUES (?,?,?,?,?,?)""",
            (sid, flag_date, "st", "medium", f"ST标记: {name}", "name_rule"),
        )
        if conn.total_changes:
            n += 1
    return n


def _scan_announcement_flags(conn: sqlite3.Connection, flag_date: str) -> int:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(stock_announcements)").fetchall()}
    if "event_type" not in cols:
        return 0
    mapping = {
        "investigation": ("investigation", "high", "立案/调查类公告"),
        "litigation": ("investigation", "high", "诉讼/立案类公告"),
        "non_standard_audit": ("non_standard_audit", "high", "年报审计非标"),
    }
    n = 0
    for et, (flag_type, severity, label) in mapping.items():
        rows = conn.execute(
            """SELECT stock_id, title, pub_date FROM stock_announcements
               WHERE event_type=? AND pub_date >= date(?, '-180 days')
               ORDER BY pub_date DESC""",
            (et, flag_date),
        ).fetchall()
        for sid, title, pub in rows:
            conn.execute(
                """INSERT OR IGNORE INTO risk_flags
                (stock_id, flag_date, flag_type, severity, detail, source)
                VALUES (?,?,?,?,?,?)""",
                (sid, pub or flag_date, flag_type, severity, f"{label}: {title[:120]}", "announcement"),
            )
            if conn.total_changes:
                n += 1
    return n


def _scan_limit_down_streak(conn: sqlite3.Connection, flag_date: str, min_days: int = 3) -> int:
    stocks = conn.execute(
        "SELECT id, code, name FROM stocks WHERE is_active=1"
    ).fetchall()
    n = 0
    for sid, code, name in stocks:
        quotes = conn.execute(
            """SELECT trade_date, change_pct, volume, turnover
               FROM stock_daily_quotes WHERE stock_id=?
               ORDER BY trade_date DESC LIMIT ?""",
            (sid, min_days + 2),
        ).fetchall()
        if len(quotes) < min_days:
            continue
        thresh = _limit_down_threshold(code, name or "")
        streak = 0
        low_vol_days = 0
        for _, chg, vol, turn in quotes[:min_days]:
            if chg is None or chg > -thresh:
                break
            streak += 1
            if (vol or 0) <= 0 or (turn or 0) < 0.5:
                low_vol_days += 1
        if streak >= min_days and low_vol_days >= min_days - 1:
            detail = f"连续{streak}日跌停且量能偏低 (阈值 {thresh}%)"
            conn.execute(
                """INSERT OR IGNORE INTO risk_flags
                (stock_id, flag_date, flag_type, severity, detail, source)
                VALUES (?,?,?,?,?,?)""",
                (sid, flag_date, "limit_down_streak", "high", detail, "daily_quotes"),
            )
            if conn.total_changes:
                n += 1
    return n


def scan_risk_flags(
    stock_ids: list[int] | None = None,
    *,
    flag_date: str | None = None,
) -> dict:
    """扫描并写入 risk_flags。"""
    as_of = flag_date or latest_trading_date()
    conn = sqlite3.connect(config.DB_PATH)
    try:
        st_n = _scan_st_flags(conn, as_of)
        ann_n = _scan_announcement_flags(conn, as_of)
        limit_n = _scan_limit_down_streak(conn, as_of)
        conn.commit()
        return {
            "flag_date": as_of,
            "st": st_n,
            "announcement": ann_n,
            "limit_down_streak": limit_n,
            "total": st_n + ann_n + limit_n,
        }
    finally:
        conn.close()


def get_risk_flags(stock_id: int, *, limit: int = 20) -> list[dict]:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT stock_id, flag_date, flag_type, severity, detail, source
               FROM risk_flags WHERE stock_id=?
               ORDER BY flag_date DESC LIMIT ?""",
            (stock_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def has_veto_risk(stock_id: int, *, within_days: int = 180) -> bool:
    """是否存在 V5 一票否决类风险标记。"""
    conn = sqlite3.connect(config.DB_PATH)
    try:
        row = conn.execute(
            """SELECT 1 FROM risk_flags
               WHERE stock_id=? AND flag_type IN ('investigation','non_standard_audit','limit_down_streak','st')
                 AND flag_date >= date('now', ?)
               LIMIT 1""",
            (stock_id, f"-{within_days} days"),
        ).fetchone()
        return row is not None
    finally:
        conn.close()
