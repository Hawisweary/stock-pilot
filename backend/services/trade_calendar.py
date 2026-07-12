"""本地交易日历读取（数据来自 trade_calendar 表，由 scripts/tushare_sync_trade_calendar.py 同步）。

进程内缓存一份 set，避免每次判断交易日都查库；trade_calendar 数据极少变动
（交易所日历一旦公布基本不回溯修改），缓存过期风险可忽略。
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import date, timedelta

import config

_lock = threading.Lock()
_open_dates: set[str] | None = None


def _load() -> set[str]:
    global _open_dates
    with _lock:
        if _open_dates is None:
            conn = sqlite3.connect(config.DB_PATH)
            rows = conn.execute(
                "SELECT cal_date FROM trade_calendar WHERE is_open=1"
            ).fetchall()
            conn.close()
            _open_dates = {r[0] for r in rows}
        return _open_dates


def is_trading_day(d: date) -> bool:
    """本地交易日历是否为交易日；表为空（未同步过）时退化为周一到周五的近似判断。"""
    open_dates = _load()
    if not open_dates:
        return d.weekday() < 5
    return d.isoformat() in open_dates


def next_trading_day(d: date, max_days: int = 30) -> date:
    """d 之后（不含 d）的下一个交易日；表为空时退化为下一个工作日。"""
    cur = d
    for _ in range(max_days):
        cur = cur + timedelta(days=1)
        if is_trading_day(cur):
            return cur
    return cur


def invalidate_cache() -> None:
    """交易日历重新同步后调用，清空进程内缓存。"""
    global _open_dates
    with _lock:
        _open_dates = None
