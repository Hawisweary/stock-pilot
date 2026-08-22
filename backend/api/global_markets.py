"""全球市场总貌 API。缓存进 us.db(global_cache),TTL 2h;K线复用 /api/us/{ticker}/klines。"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3

from fastapi import APIRouter, Query

import config

router = APIRouter(prefix="/api/global", tags=["global"])


@router.get("/overview")
def overview(refresh: bool = Query(False, description="强制刷新")):
    """全球股指/汇率/大宗/美债/加密总貌 + 多周期涨跌。缓存 2h。"""
    conn = sqlite3.connect(config.US_DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS global_cache (id INTEGER PRIMARY KEY, data TEXT, updated_at TEXT)")
        row = conn.execute("SELECT data,updated_at FROM global_cache WHERE id=1").fetchone()
        if row and not refresh:
            try:
                age = (dt.datetime.now() - dt.datetime.fromisoformat(row["updated_at"])).total_seconds()
                if age < 2 * 3600:
                    return json.loads(row["data"])
            except (ValueError, TypeError):
                pass
        try:
            from services.global_markets import fetch_global_overview
            data = fetch_global_overview()
        except Exception as ex:  # noqa: BLE001  抓取失败退回旧缓存
            if row:
                return json.loads(row["data"])
            return {"as_of": None, "groups": [], "error": str(ex)[:100]}
        conn.execute("INSERT OR REPLACE INTO global_cache(id,data,updated_at) VALUES(1,?,?)",
                     (json.dumps(data, ensure_ascii=False), dt.datetime.now().isoformat()))
        conn.commit()
        return data
    finally:
        conn.close()
