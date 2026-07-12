"""因子数据质量 — 未来函数标记与过滤"""
from __future__ import annotations

from typing import Optional

from services.financial_calendar import is_fundamental_valid_at, rebuild_from_financial_reports

LOOKAHEAD_FACTORS = {"F002", "fundamental_score"}


def ensure_quality_prerequisites() -> dict:
    """重建披露日历（幂等）。"""
    return {"financial_calendar": rebuild_from_financial_reports()}


def is_factor_value_valid(
    factor_id: str,
    stock_id: int,
    calc_date: str,
) -> tuple[bool, Optional[str]]:
    """
    返回 (valid, flag)。
    flag: None=ok, 'look_ahead_fundamental', 'unknown_calendar'
    """
    fid = factor_id if factor_id.startswith("F") else None
    if fid == "F002" or factor_id == "fundamental_score":
        valid = is_fundamental_valid_at(stock_id, calc_date)
        if not valid:
            return False, "look_ahead_fundamental"
        from config import DB_PATH
        import sqlite3

        conn = sqlite3.connect(DB_PATH)
        has_cal = conn.execute(
            "SELECT 1 FROM financial_calendar WHERE stock_id=? LIMIT 1", (stock_id,)
        ).fetchone()
        conn.close()
        if not has_cal:
            return True, "unknown_calendar"
    return True, None


def filter_fundamental_for_backfill(stock_id: int, calc_date: str, value: Optional[float]) -> Optional[float]:
    """回填 F002 时剔除未来函数值。"""
    if value is None:
        return None
    valid, _ = is_factor_value_valid("F002", stock_id, calc_date)
    return float(value) if valid else None
