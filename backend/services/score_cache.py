"""评分缓存读写 — 单股读缓存、批量持久化"""
from __future__ import annotations

import sqlite3
from typing import Optional

from config import DB_PATH
from services.comprehensive_store import upsert_dimension_score


def get_latest_dimension(table: str, stock_id: int, date_col: str = "date") -> Optional[dict]:
    """从 capital_scores / sentiment_scores / policy_scores 读最新一行。"""
    allowed = {"capital_scores", "sentiment_scores", "policy_scores"}
    if table not in allowed:
        return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            f"SELECT * FROM [{table}] WHERE stock_id=? ORDER BY [{date_col}] DESC LIMIT 1",
            (stock_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def persist_capital_rows(rows: list[dict], date_str: str) -> int:
    if not rows:
        return 0
    conn = sqlite3.connect(DB_PATH)
    count = 0
    try:
        for r in rows:
            conn.execute(
                """INSERT OR REPLACE INTO capital_scores
                (stock_id, date, composite_score, flow_score, turnover_score,
                 volume_score, change_score, holder_score, breakdown_json)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    r["stock_id"],
                    r.get("date", date_str),
                    r.get("composite_score", r.get("score", 0)),
                    r.get("flow_score", 0),
                    r.get("turnover_score", r.get("turn_score", 0)),
                    r.get("volume_score", 0),
                    r.get("change_score", 0),
                    r.get("holder_score", 0),
                    r.get("breakdown_json", "{}"),
                ),
            )
            count += 1
        conn.commit()
    finally:
        conn.close()
    return count


def persist_sentiment_rows(rows: list[dict], date_str: str) -> int:
    if not rows:
        return 0
    conn = sqlite3.connect(DB_PATH)
    count = 0
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS sentiment_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT, stock_id INTEGER NOT NULL,
            date TEXT NOT NULL, composite_score REAL, turnover_score REAL,
            leverage_score REAL, limit_score REAL, rsi_score REAL,
            breakdown_json TEXT, UNIQUE(stock_id, date))"""
        )
        for r in rows:
            score = r.get("composite_score", r.get("score", 0))
            conn.execute(
                """INSERT OR REPLACE INTO sentiment_scores
                (stock_id, date, composite_score, turnover_score, leverage_score,
                 limit_score, rsi_score, breakdown_json)
                VALUES (?,?,?,?,?,?,?,?)""",
                (
                    r["stock_id"],
                    date_str,
                    score,
                    r.get("turn_score", r.get("turnover_score", 0)),
                    r.get("vol_score", 0),
                    0,
                    0,
                    r.get("breakdown_json", "{}"),
                ),
            )
            count += 1
        conn.commit()
    finally:
        conn.close()
    return count


def sync_comprehensive_column(table: str, column: str, calc_date: str) -> int:
    conn = sqlite3.connect(DB_PATH)
    synced = 0
    try:
        rows = conn.execute(
            f"SELECT stock_id, composite_score FROM [{table}] WHERE date=? AND composite_score IS NOT NULL",
            (calc_date,),
        ).fetchall()
    finally:
        conn.close()
    for stock_id, score in rows:
        upsert_dimension_score(int(stock_id), column, float(score), calc_date=calc_date)
        synced += 1
    return synced
