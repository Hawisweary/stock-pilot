"""同步上市公司基本信息（stock_company）+ 管理层名单（stk_managers）。

公司信息按交易所批量拉取（3 次调用覆盖全市场），管理层只能按单只股票查询，
变动很慢，属于低频批量任务，不需要每天重跑。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from services.tushare_adapter import fetch_company_info_bulk, fetch_managers


def _code_map(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT id, code FROM stocks").fetchall()
    return {code: sid for sid, code in rows}


def _strip_suffix(ts_code: str) -> str:
    return ts_code.split(".")[0]


def sync_company_info(conn: sqlite3.Connection, code_map: dict[str, int]) -> None:
    rows: list[tuple] = []
    for exchange in ("SSE", "SZSE", "BSE"):
        records = fetch_company_info_bulk(exchange)
        print(f"[公司信息] {exchange}: {len(records)} 条")
        for r in records:
            code = _strip_suffix(r["ts_code"])
            stock_id = code_map.get(code)
            if stock_id is None:
                continue
            rows.append((
                stock_id, r.get("com_name") or "", r.get("chairman") or "",
                r.get("manager") or "", r.get("secretary") or "",
                r.get("reg_capital"), r.get("setup_date") or "",
                r.get("province") or "", r.get("city") or "",
                r.get("website") or "", r.get("employees"),
                r.get("main_business") or "", r.get("business_scope") or "",
                r.get("introduction") or "",
            ))

    conn.executemany(
        """INSERT OR REPLACE INTO stock_company_info
           (stock_id, com_name, chairman, manager, secretary, reg_capital,
            setup_date, province, city, website, employees, main_business,
            business_scope, introduction, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
        rows,
    )
    conn.commit()
    print(f"[公司信息] 写入 {len(rows)} 条")


def sync_managers(conn: sqlite3.Connection) -> None:
    stocks = conn.execute("SELECT id, code, market FROM stocks WHERE is_active=1").fetchall()
    from services.tushare_adapter import code_to_ts_code

    ok, empty = 0, 0
    for i, (stock_id, code, market) in enumerate(stocks, 1):
        ts_code = code_to_ts_code(code, market if market in ("BJ",) else None)
        records = fetch_managers(ts_code)
        if records:
            rows = [
                (stock_id, r.get("name") or "", r.get("lev") or "", r.get("title") or "",
                 r.get("gender") or "", r.get("edu") or "", r.get("birthday") or "",
                 r.get("begin_date") or "", r.get("end_date") or "")
                for r in records if r.get("name")
            ]
            conn.executemany(
                """INSERT OR REPLACE INTO stock_managers
                   (stock_id, name, lev, title, gender, edu, birthday, begin_date, end_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            conn.commit()
            ok += 1
        else:
            empty += 1
        if i % 200 == 0:
            print(f"[管理层] {i}/{len(stocks)} (有数据 {ok} / 无数据 {empty})")
    print(f"[管理层] 完成，共 {len(stocks)} 只股票，有数据 {ok} 只")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-company", action="store_true")
    parser.add_argument("--skip-managers", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(config.DB_PATH)
    code_map = _code_map(conn)

    if not args.skip_company:
        sync_company_info(conn, code_map)
    if not args.skip_managers:
        sync_managers(conn)

    conn.close()


if __name__ == "__main__":
    main()
