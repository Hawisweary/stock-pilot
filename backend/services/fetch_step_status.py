"""每股抓取步骤状态 — 供 UI 标记待修复 / 熔断跳过。"""
from __future__ import annotations

import sqlite3

import config
from database import write_lock


def _connect() -> sqlite3.Connection:
    db = sqlite3.connect(config.DB_PATH)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=30000")
    db.execute("PRAGMA synchronous=NORMAL")
    return db


def record_step(
    stock_id: int,
    step: str,
    status: str,
    message: str = "",
    *,
    conn: sqlite3.Connection | None = None,
) -> None:
    own = conn is None
    db = conn or _connect()
    sql = """
        INSERT INTO fetch_step_status (stock_id, step, status, message, updated_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        ON CONFLICT(stock_id, step) DO UPDATE SET
            status=excluded.status,
            message=excluded.message,
            updated_at=datetime('now')
    """
    payload = (stock_id, step, status, (message or "")[:500])
    try:
        if own:
            with write_lock:
                db.execute(sql, payload)
                db.commit()
        else:
            db.execute(sql, payload)
    finally:
        if own:
            db.close()


def get_summary(stock_ids: list[int] | None = None) -> dict[int, dict[str, dict]]:
    db = _connect()
    try:
        if stock_ids:
            ph = ",".join("?" * len(stock_ids))
            rows = db.execute(
                f"SELECT stock_id, step, status, message, updated_at FROM fetch_step_status WHERE stock_id IN ({ph})",
                stock_ids,
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT stock_id, step, status, message, updated_at FROM fetch_step_status"
            ).fetchall()
        out: dict[int, dict[str, dict]] = {}
        for r in rows:
            sid = int(r[0])
            out.setdefault(sid, {})[str(r[1])] = {
                "status": r[2],
                "message": r[3] or "",
                "updated_at": r[4],
            }
        return out
    finally:
        db.close()
