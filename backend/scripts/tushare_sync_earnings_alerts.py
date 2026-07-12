"""同步业绩预告（forecast_vip）+ 业绩快报（express_vip）到本地表。

两者都是官方自愿/条件披露数据，覆盖率明显低于正式财报（预告仅业绩大幅变动/
亏损等情形强制披露，快报纯自愿），按报告期全市场批量拉取。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from services.tushare_adapter import fetch_forecast_vip, fetch_express_vip


def _quarter_ends(years: int) -> list[str]:
    from datetime import date
    today = date.today()
    ends = []
    for yy in range(today.year - years, today.year + 1):
        for md in ("0331", "0630", "0930", "1231"):
            d = f"{yy}{md}"
            if d <= today.strftime("%Y%m%d"):
                ends.append(d)
    return ends


def _code_map(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT id, code FROM stocks").fetchall()
    return {code: sid for sid, code in rows}


def sync_forecast(conn: sqlite3.Connection, code_map: dict[str, int], periods: list[str]) -> int:
    total = 0
    for i, period in enumerate(periods, 1):
        data = fetch_forecast_vip(period)
        period_end = f"{period[:4]}-{period[4:6]}-{period[6:8]}"
        rows = []
        for ts_code, d in data.items():
            code = ts_code.split(".")[0]
            stock_id = code_map.get(code)
            if stock_id is None:
                continue
            rows.append((
                stock_id, period_end, d["ann_date"], d["type"], d["p_change_min"],
                d["p_change_max"], d["net_profit_min"], d["net_profit_max"],
                d["last_parent_net"], d["summary"], d["change_reason"],
            ))
        if rows:
            conn.executemany(
                """INSERT OR REPLACE INTO earnings_forecast
                   (stock_id, period_end_date, ann_date, type, p_change_min, p_change_max,
                    net_profit_min, net_profit_max, last_parent_net, summary, change_reason)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
            conn.commit()
            total += len(rows)
        print(f"[业绩预告] [{i}/{len(periods)}] {period}: {len(rows)} 条")
    return total


def sync_express(conn: sqlite3.Connection, code_map: dict[str, int], periods: list[str]) -> int:
    total = 0
    for i, period in enumerate(periods, 1):
        data = fetch_express_vip(period)
        period_end = f"{period[:4]}-{period[4:6]}-{period[6:8]}"
        rows = []
        for ts_code, d in data.items():
            code = ts_code.split(".")[0]
            stock_id = code_map.get(code)
            if stock_id is None:
                continue
            rows.append((
                stock_id, period_end, d["ann_date"], d["revenue"], d["operate_profit"],
                d["n_income"], d["total_assets"], d["diluted_eps"], d["diluted_roe"],
                d["yoy_sales"], d["yoy_dedu_np"], d["perf_summary"],
            ))
        if rows:
            conn.executemany(
                """INSERT OR REPLACE INTO earnings_express
                   (stock_id, period_end_date, ann_date, revenue, operate_profit, n_income,
                    total_assets, diluted_eps, diluted_roe, yoy_sales, yoy_dedu_np, perf_summary)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
            conn.commit()
            total += len(rows)
        print(f"[业绩快报] [{i}/{len(periods)}] {period}: {len(rows)} 条")
    return total


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=1, help="回补年数（默认1）")
    parser.add_argument("--skip-forecast", action="store_true")
    parser.add_argument("--skip-express", action="store_true")
    args = parser.parse_args()

    periods = _quarter_ends(args.years)
    print(f"报告期: {len(periods)} 个 ({periods[0]}~{periods[-1]})")

    conn = sqlite3.connect(config.DB_PATH)
    code_map = _code_map(conn)

    if not args.skip_forecast:
        n = sync_forecast(conn, code_map, periods)
        print(f"[业绩预告] 共写入 {n} 条")
    if not args.skip_express:
        n = sync_express(conn, code_map, periods)
        print(f"[业绩快报] 共写入 {n} 条")

    conn.close()


if __name__ == "__main__":
    main()
