"""因子IC分析结果缓存(cache.db持久化) — 单次全量重算约13秒,按数据日期缓存+每日预热"""
from __future__ import annotations

import json
import logging
import sqlite3

from config import DB_PATH

logger = logging.getLogger(__name__)


def latest_factor_date(factor_id: str) -> str:
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT MAX(date) FROM factor_values WHERE factor_id=?", (factor_id,)
        ).fetchone()
        return row[0] or ""
    finally:
        conn.close()


def get_cached(factor_id: str, forward_days: int, data_date: str) -> dict | None:
    """数据日期一致才命中。"""
    from database import cache_connect

    if not data_date:
        return None
    conn = cache_connect()
    try:
        row = conn.execute(
            """SELECT result_json FROM factor_analysis_cache
               WHERE factor_id=? AND forward_days=? AND data_date=?""",
            (factor_id, forward_days, data_date),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except Exception:
        return None


def store(factor_id: str, forward_days: int, data_date: str, result: dict) -> None:
    from database import cache_connect

    if not data_date or not isinstance(result, dict) or result.get("error"):
        return
    conn = cache_connect()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO factor_analysis_cache
               (factor_id, forward_days, data_date, result_json, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'))""",
            (factor_id, forward_days, data_date, json.dumps(result, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


def compute_and_cache(factor_id: str, forward_days: int = 20) -> dict:
    """算一次并落缓存(API与预热共用入口)。"""
    from services.factor_factory import factor_extended_analysis

    data_date = latest_factor_date(factor_id)
    cached = get_cached(factor_id, forward_days, data_date)
    if cached is not None:
        return cached
    result = factor_extended_analysis(factor_id, forward_days=forward_days)
    store(factor_id, forward_days, data_date, result)
    return result


def warm_all(forward_days: int = 20) -> dict:
    """预热全部注册因子的默认分析(每日流水线末尾调用)。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        fids = [
            r[0]
            for r in conn.execute(
                "SELECT factor_id FROM factor_registry ORDER BY factor_id"
            ).fetchall()
        ]
    finally:
        conn.close()

    warmed = 0
    errors = 0
    for fid in fids:
        try:
            compute_and_cache(fid, forward_days)
            warmed += 1
        except Exception as e:
            errors += 1
            logger.warning("因子分析预热失败 %s: %s", fid, e)
    return {"warmed": warmed, "errors": errors, "total": len(fids)}
