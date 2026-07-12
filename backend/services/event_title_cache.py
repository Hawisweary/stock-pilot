"""公告/新闻标题 → event_type 持久化缓存（跨次 LLM 复用）。"""
from __future__ import annotations

import sqlite3
from typing import Iterable

import config

_TABLE = "event_title_cache"


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""CREATE TABLE IF NOT EXISTS {_TABLE} (
            title_key TEXT PRIMARY KEY,
            event_type TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'llm',
            hit_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )"""
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{_TABLE}_updated ON {_TABLE}(updated_at DESC)"
    )


def lookup_titles(
    title_keys: Iterable[str],
    *,
    conn: sqlite3.Connection | None = None,
) -> dict[str, str]:
    """批量查缓存，返回 title_key -> event_type（含空串）。"""
    keys = [k for k in dict.fromkeys(title_keys) if k]
    if not keys:
        return {}

    own_conn = conn is None
    db = conn or sqlite3.connect(config.DB_PATH)
    try:
        ensure_table(db)
        out: dict[str, str] = {}
        chunk = 200
        for i in range(0, len(keys), chunk):
            part = keys[i : i + chunk]
            ph = ",".join("?" * len(part))
            rows = db.execute(
                f"SELECT title_key, event_type FROM {_TABLE} WHERE title_key IN ({ph})",
                part,
            ).fetchall()
            for key, et in rows:
                out[str(key)] = str(et or "")
        if out:
            _bump_hits(db, list(out.keys()), commit=own_conn)
        return out
    finally:
        if own_conn:
            db.close()


def store_titles(
    mapping: dict[str, str],
    *,
    source: str = "llm",
    conn: sqlite3.Connection | None = None,
) -> int:
    """写入/更新缓存。空 event_type 也缓存，避免重复调 LLM。"""
    if not mapping:
        return 0

    own_conn = conn is None
    db = conn or sqlite3.connect(config.DB_PATH)
    try:
        ensure_table(db)
        n = 0
        for title_key, event_type in mapping.items():
            if not title_key:
                continue
            db.execute(
                f"""INSERT INTO {_TABLE} (title_key, event_type, source, hit_count, updated_at)
                    VALUES (?, ?, ?, 1, datetime('now'))
                    ON CONFLICT(title_key) DO UPDATE SET
                      event_type=excluded.event_type,
                      source=excluded.source,
                      updated_at=datetime('now')""",
                (title_key, str(event_type or ""), source),
            )
            n += 1
        if own_conn:
            db.commit()
        return n
    finally:
        if own_conn:
            db.close()


def _bump_hits(conn: sqlite3.Connection, keys: list[str], *, commit: bool = True) -> None:
    if not keys:
        return
    chunk = 200
    for i in range(0, len(keys), chunk):
        part = keys[i : i + chunk]
        ph = ",".join("?" * len(part))
        conn.execute(
            f"""UPDATE {_TABLE}
                SET hit_count = hit_count + 1, updated_at = datetime('now')
                WHERE title_key IN ({ph})""",
            part,
        )
    if commit:
        conn.commit()
