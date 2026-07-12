"""同步财报披露计划（disclosure_date）到 financial_calendar，替换法定截止日估算。

公司自己预约/实际披露的日期（pre_date/actual_date）比统计口径的"法定最晚
截止日"估算准确得多，且 UNIQUE(stock_id, period_end_date, report_type) 约束下
INSERT OR REPLACE 会直接覆盖旧的 conservative+45 估算行。
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from services.financial_calendar import ensure_tables
from services.tushare_adapter import fetch_disclosure_date


def _quarter_ends(years_back: int, years_fwd: int) -> list[str]:
    today = date.today()
    ends = []
    for yy in range(today.year - years_back, today.year + years_fwd + 1):
        for md in ("0331", "0630", "0930", "1231"):
            ends.append(f"{yy}{md}")
    return ends


def _code_map(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT id, code FROM stocks").fetchall()
    return {code: sid for sid, code in rows}


def main() -> None:
    conn = sqlite3.connect(config.DB_PATH)
    ensure_tables(conn)
    code_map = _code_map(conn)

    periods = _quarter_ends(years_back=1, years_fwd=1)
    # 新建行用 q1/q2/q3/annual（api/calendar.py::REPORT_TYPE_LABEL 能正确翻译显示名），
    # 不用 financial_reports 里的 "quarterly"（那个值不在 REPORT_TYPE_LABEL 映射表里，
    # 前端会直接显示英文原文）。已存在的行走 UPDATE 分支，不受此影响。
    _MONTH_TO_TYPE = {"03": "q1", "06": "q2", "09": "q3", "12": "annual"}
    fallback_report_type = {p: _MONTH_TO_TYPE[p[4:6]] for p in periods}

    total_updated = total_inserted = 0
    for i, period in enumerate(periods, 1):
        data = fetch_disclosure_date(period)
        period_end = f"{period[:4]}-{period[4:6]}-{period[6:8]}"
        updated = inserted = 0
        for ts_code, d in data.items():
            code = ts_code.split(".")[0]
            stock_id = code_map.get(code)
            if stock_id is None:
                continue
            disclosure = d["actual_date"] or d["pre_date"]
            if not disclosure:
                continue
            cur = conn.execute(
                """UPDATE financial_calendar SET disclosure_date=?, source='tushare'
                   WHERE stock_id=? AND period_end_date=?""",
                (disclosure, stock_id, period_end),
            )
            if cur.rowcount:
                updated += cur.rowcount
            else:
                conn.execute(
                    """INSERT OR IGNORE INTO financial_calendar
                       (stock_id, period_end_date, report_type, disclosure_date, source)
                       VALUES (?,?,?,?,'tushare')""",
                    (stock_id, period_end, fallback_report_type[period], disclosure),
                )
                inserted += 1
        conn.commit()
        total_updated += updated
        total_inserted += inserted
        print(f"[披露计划] [{i}/{len(periods)}] {period}: 更新 {updated} / 新增 {inserted}")

    conn.close()
    print(f"完成，共更新 {total_updated} 条 / 新增 {total_inserted} 条")


if __name__ == "__main__":
    main()
