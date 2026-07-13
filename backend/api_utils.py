"""
AI基本面研究员 - API 工具函数
提供数据库连接获取和行数据转换等公共方法
"""
from __future__ import annotations

import sqlite3
from database import get, write_lock


def get_db() -> tuple[sqlite3.Connection, sqlite3.Cursor]:
    """获取数据库连接和游标"""
    conn = get()
    return conn, conn.cursor()


def row2dict(row: sqlite3.Row | None) -> dict | None:
    """将 sqlite3.Row 转换为字典"""
    if row is None:
        return None
    return dict(row)


def rows2list(rows: list[sqlite3.Row]) -> list[dict]:
    """将 sqlite3.Row 列表转换为字典列表"""
    return [dict(row) for row in rows]


def execute_sql(sql: str, params: tuple = ()) -> list[dict]:
    """执行查询并返回字典列表（data_fetch_log 等缓存表自动路由到 cache.db）"""
    if "data_fetch_log" in sql:
        from database import cache_connect

        cconn = cache_connect()
        try:
            return rows2list(cconn.execute(sql, params).fetchall())
        finally:
            cconn.close()
    conn, cur = get_db()
    cur.execute(sql, params)
    rows = cur.fetchall()
    return rows2list(rows)


def execute_insert(sql: str, params: tuple = ()) -> int:
    """执行插入并返回 lastrowid"""
    with write_lock:
        conn, cur = get_db()
        cur.execute(sql, params)
        conn.commit()
        return cur.lastrowid or 0


def execute_update(sql: str, params: tuple = ()) -> int:
    """执行更新并返回影响行数"""
    with write_lock:
        conn, cur = get_db()
        cur.execute(sql, params)
        conn.commit()
        return cur.rowcount
