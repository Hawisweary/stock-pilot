"""同步个股资金流明细：L2 大小单口径（moneyflow）+ 东方财富口径（moneyflow_dc）。

两者是独立数据源（一个基于交易所逐笔委托统计，一个是东财自己的算法），
按交易日全市场批量拉取，写入各自独立的表，供个股详情页展示对比。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from services.tushare_adapter import fetch_market_fund_flow_l2_detail, fetch_market_fund_flow_dc


def _trading_days(start: str, end: str) -> list[str]:
    from services.tushare_adapter import _pro, _throttle

    pro = _pro()
    _throttle()
    df = pro.trade_cal(exchange="SSE", start_date=start, end_date=end, is_open="1")
    if df is None or df.empty:
        return []
    return sorted(df["cal_date"].astype(str).tolist())


def _code_map(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT id, code FROM stocks").fetchall()
    return {code: sid for sid, code in rows}


def sync_l2(conn: sqlite3.Connection, code_map: dict[str, int], trading_days: list[str]) -> int:
    total = 0
    for i, d in enumerate(trading_days, 1):
        data = fetch_market_fund_flow_l2_detail(d)
        trade_date_fmt = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        rows = []
        for ts_code, v in data.items():
            code = ts_code.split(".")[0]
            stock_id = code_map.get(code)
            if stock_id is None:
                continue
            rows.append((
                stock_id, trade_date_fmt, v["buy_sm_amount"], v["sell_sm_amount"],
                v["buy_md_amount"], v["sell_md_amount"], v["buy_lg_amount"], v["sell_lg_amount"],
                v["buy_elg_amount"], v["sell_elg_amount"], v["net_mf_amount"],
            ))
        if rows:
            conn.executemany(
                """INSERT OR REPLACE INTO stock_moneyflow_l2_daily
                   (stock_id, trade_date, buy_sm_amount, sell_sm_amount, buy_md_amount,
                    sell_md_amount, buy_lg_amount, sell_lg_amount, buy_elg_amount,
                    sell_elg_amount, net_mf_amount)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
            conn.commit()
            total += len(rows)
        print(f"[L2大小单] [{i}/{len(trading_days)}] {d}: {len(rows)} 条")
    return total


def sync_dc(conn: sqlite3.Connection, code_map: dict[str, int], trading_days: list[str]) -> int:
    total = 0
    for i, d in enumerate(trading_days, 1):
        data = fetch_market_fund_flow_dc(d)
        trade_date_fmt = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        rows = []
        for ts_code, v in data.items():
            code = ts_code.split(".")[0]
            stock_id = code_map.get(code)
            if stock_id is None:
                continue
            rows.append((
                stock_id, trade_date_fmt, v["net_amount"], v["net_amount_rate"],
                v["buy_elg_amount"], v["buy_lg_amount"], v["buy_md_amount"], v["buy_sm_amount"],
            ))
        if rows:
            conn.executemany(
                """INSERT OR REPLACE INTO stock_moneyflow_dc_daily
                   (stock_id, trade_date, net_amount, net_amount_rate, buy_elg_amount,
                    buy_lg_amount, buy_md_amount, buy_sm_amount)
                   VALUES (?,?,?,?,?,?,?,?)""",
                rows,
            )
            conn.commit()
            total += len(rows)
        print(f"[东财资金流] [{i}/{len(trading_days)}] {d}: {len(rows)} 条")
    return total


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=10, help="回补最近 N 个交易日（默认10）")
    parser.add_argument("--skip-l2", action="store_true")
    parser.add_argument("--skip-dc", action="store_true")
    args = parser.parse_args()

    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=int(args.days * 1.6) + 10)).strftime("%Y%m%d")
    trading_days = _trading_days(start, end)[-args.days:]
    print(f"资金流明细: {len(trading_days)} 个交易日 ({trading_days[0]}~{trading_days[-1]})")

    conn = sqlite3.connect(config.DB_PATH)
    code_map = _code_map(conn)

    if not args.skip_l2:
        n = sync_l2(conn, code_map, trading_days)
        print(f"[L2大小单] 共写入 {n} 条")
    if not args.skip_dc:
        n = sync_dc(conn, code_map, trading_days)
        print(f"[东财资金流] 共写入 {n} 条")

    conn.close()


if __name__ == "__main__":
    main()
