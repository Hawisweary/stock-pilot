#!/usr/bin/env python3
"""一次性修复：tushare 来源的 main_net_inflow/super_large_inflow 单位错误（万元误当元存储）。

问题：fetch_market_fund_flow() 之前未把 Tushare moneyflow 的万元字段换算成元，
导致 stock_fund_flow_daily 里 source='tushare' 的行比 source='eastmoney' 的行小 10000 倍。
main_net_5d 是跨 source 的滚动窗口聚合（按 stock_id, trade_date 排序，不区分 source），
所以过渡期附近的 5 日汇总还会把两种单位混算，需要整体重算。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3

from config import DB_PATH


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM stock_fund_flow_daily WHERE source='tushare'"
        )
        n_before = cur.fetchone()[0]
        print(f"待修复 tushare 行数: {n_before}")

        conn.execute(
            """UPDATE stock_fund_flow_daily
               SET main_net_inflow = main_net_inflow * 10000
               WHERE source='tushare' AND main_net_inflow IS NOT NULL"""
        )
        conn.execute(
            """UPDATE stock_fund_flow_daily
               SET super_large_inflow = super_large_inflow * 10000
               WHERE source='tushare' AND super_large_inflow IS NOT NULL"""
        )
        conn.commit()
        print("main_net_inflow / super_large_inflow 已按 ×10000 修正")

        stock_ids = [
            r[0] for r in conn.execute("SELECT id FROM stocks").fetchall()
        ]
        ph = ",".join("?" * len(stock_ids))
        conn.execute(
            f"""
            WITH ranked AS (
                SELECT rowid AS rid,
                       SUM(main_net_inflow) OVER (
                           PARTITION BY stock_id ORDER BY trade_date
                           ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
                       ) AS net5
                FROM stock_fund_flow_daily
                WHERE stock_id IN ({ph})
            )
            UPDATE stock_fund_flow_daily
            SET main_net_5d = (SELECT net5 FROM ranked WHERE ranked.rid = stock_fund_flow_daily.rowid)
            WHERE rowid IN (SELECT rid FROM ranked)
            """,
            stock_ids,
        )
        conn.commit()
        print(f"main_net_5d 已对 {len(stock_ids)} 只股票整体重算")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
