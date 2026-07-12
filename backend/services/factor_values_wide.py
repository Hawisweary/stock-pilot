"""因子宽表 factor_values_wide — f001..f015 列存储 + EAV 双写"""
from __future__ import annotations

import sqlite3
from typing import Dict, Optional

from config import DB_PATH

FACTOR_ID_TO_COL = {f"F{i:03d}": f"f{i:03d}" for i in range(1, 16)}
COL_TO_FACTOR_ID = {v: k for k, v in FACTOR_ID_TO_COL.items()}

WIDE_COLUMNS = [f"f{i:03d}" for i in range(1, 16)]

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS factor_values_wide (
    stock_id INTEGER NOT NULL,
    calc_date TEXT NOT NULL,
    f001 REAL, f002 REAL, f003 REAL, f004 REAL, f005 REAL,
    f006 REAL, f007 REAL, f008 REAL, f009 REAL, f010 REAL,
    f011 REAL, f012 REAL, f013 REAL, f014 REAL, f015 REAL,
    quality_flags TEXT DEFAULT '{}',
    updated_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (stock_id, calc_date)
);
CREATE INDEX IF NOT EXISTS idx_fvw_date ON factor_values_wide(calc_date);
CREATE INDEX IF NOT EXISTS idx_fvw_stock ON factor_values_wide(stock_id, calc_date DESC);
"""


def ensure_tables(conn: Optional[sqlite3.Connection] = None) -> None:
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    conn.executescript(CREATE_SQL)
    conn.commit()
    if own:
        conn.close()


def upsert_wide_factor(
    conn: sqlite3.Connection,
    stock_id: int,
    calc_date: str,
    factor_id: str,
    value: Optional[float],
    quality_flag: Optional[str] = None,
) -> None:
    """写入单列；与 EAV 双写。"""
    ensure_tables(conn)
    col = FACTOR_ID_TO_COL.get(factor_id)
    if not col:
        return

    exists = conn.execute(
        "SELECT quality_flags FROM factor_values_wide WHERE stock_id=? AND calc_date=?",
        (stock_id, calc_date),
    ).fetchone()

    if not exists:
        conn.execute(
            f"""INSERT INTO factor_values_wide (stock_id, calc_date, {col}, updated_at)
                VALUES (?, ?, ?, datetime('now'))""",
            (stock_id, calc_date, value),
        )
        flags_raw = "{}"
    else:
        conn.execute(
            f"""UPDATE factor_values_wide SET {col}=?, updated_at=datetime('now')
                WHERE stock_id=? AND calc_date=?""",
            (value, stock_id, calc_date),
        )
        flags_raw = exists[0] or "{}"

    if quality_flag:
        import json

        try:
            flags = json.loads(flags_raw)
        except json.JSONDecodeError:
            flags = {}
        flags[factor_id] = quality_flag
        conn.execute(
            """UPDATE factor_values_wide SET quality_flags=?
               WHERE stock_id=? AND calc_date=?""",
            (json.dumps(flags, ensure_ascii=False), stock_id, calc_date),
        )


def upsert_wide_row(
    conn: sqlite3.Connection,
    stock_id: int,
    calc_date: str,
    factors: Dict[str, Optional[float]],
) -> None:
    ensure_tables(conn)
    existing = conn.execute(
        "SELECT 1 FROM factor_values_wide WHERE stock_id=? AND calc_date=?",
        (stock_id, calc_date),
    ).fetchone()

    if not existing:
        conn.execute(
            """INSERT INTO factor_values_wide (stock_id, calc_date, updated_at)
               VALUES (?, ?, datetime('now'))""",
            (stock_id, calc_date),
        )

    for fid, val in factors.items():
        col = FACTOR_ID_TO_COL.get(fid)
        if col and val is not None:
            conn.execute(
                f"""UPDATE factor_values_wide SET {col}=?, updated_at=datetime('now')
                    WHERE stock_id=? AND calc_date=?""",
                (val, stock_id, calc_date),
            )


def migrate_eav_to_wide(conn: Optional[sqlite3.Connection] = None) -> dict:
    """一次性：factor_values EAV → wide。"""
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    ensure_tables(conn)

    if not conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='factor_values'"
    ).fetchone():
        if own:
            conn.close()
        return {"rows": 0, "skipped": True}

    rows = conn.execute(
        """SELECT stock_id, date, factor_id, value FROM factor_values WHERE value IS NOT NULL"""
    ).fetchall()

    batch: Dict[tuple[int, str], Dict[str, float]] = {}
    for sid, dt, fid, val in rows:
        key = (sid, dt)
        batch.setdefault(key, {})[fid] = float(val)

    for (sid, dt), facs in batch.items():
        upsert_wide_row(conn, sid, dt, facs)

    wide_rows = conn.execute("SELECT COUNT(*) FROM factor_values_wide").fetchone()[0]
    if own:
        conn.commit()
        conn.close()
    return {"eav_cells": len(rows), "wide_rows": wide_rows}


def read_factor_series_from_wide(
    factor_id: str,
    conn: Optional[sqlite3.Connection] = None,
) -> list[tuple[int, str, float]]:
    """stock_id, calc_date, value"""
    col = FACTOR_ID_TO_COL.get(factor_id)
    if not col:
        return []
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    ensure_tables(conn)
    rows = conn.execute(
        f"""SELECT stock_id, calc_date, {col} FROM factor_values_wide
            WHERE {col} IS NOT NULL ORDER BY calc_date"""
    ).fetchall()
    if own:
        conn.close()
    return [(r[0], r[1], float(r[2])) for r in rows]
