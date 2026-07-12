"""同步 AKShare 特色数据：创新高/新低统计 + 龙虎榜多周期上榜统计 + 沪深市场总貌。

三个都是全市场/全周期一次性拉取，跑起来很快，建议每日跑一次。
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from services.akshare_adapter import (
    fetch_new_high_low_stats,
    fetch_lhb_period_stats,
    fetch_market_summary,
)

PERIODS = ["近一月", "近三月", "近六月", "近一年"]
PERIOD_KEY = {"近一月": "1m", "近三月": "3m", "近六月": "6m", "近一年": "1y"}


def _code_map(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT id, code FROM stocks").fetchall()
    return {code: sid for sid, code in rows}


def sync_new_high_low(conn: sqlite3.Connection) -> int:
    rows = fetch_new_high_low_stats()
    if not rows:
        return 0
    conn.executemany(
        """INSERT OR REPLACE INTO market_new_high_low_daily
           (trade_date, close, high20, low20, high60, low60, high120, low120)
           VALUES (:trade_date, :close, :high20, :low20, :high60, :low60, :high120, :low120)""",
        rows,
    )
    conn.commit()
    return len(rows)


def sync_lhb_period_stats(conn: sqlite3.Connection, code_map: dict[str, int]) -> int:
    today = date.today().isoformat()
    total = 0
    for period in PERIODS:
        rows = fetch_lhb_period_stats(period)
        db_rows = []
        for r in rows:
            stock_id = code_map.get(r["code"])
            if stock_id is None:
                continue
            db_rows.append((
                stock_id, PERIOD_KEY[period], today, r["last_lhb_date"], r["close"],
                r["change_pct"], r["lhb_count"], r["lhb_net_amount"], r["lhb_buy_amount"],
                r["lhb_sell_amount"], r["inst_buy_count"], r["inst_sell_count"],
                r["inst_net_amount"], r["chg_1m"], r["chg_3m"], r["chg_6m"], r["chg_1y"],
            ))
        if db_rows:
            conn.executemany(
                """INSERT OR REPLACE INTO stock_lhb_period_stats
                   (stock_id, period, updated_date, last_lhb_date, close, change_pct,
                    lhb_count, lhb_net_amount, lhb_buy_amount, lhb_sell_amount,
                    inst_buy_count, inst_sell_count, inst_net_amount,
                    chg_1m, chg_3m, chg_6m, chg_1y)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                db_rows,
            )
            conn.commit()
            total += len(db_rows)
        print(f"[龙虎榜多周期] {period}: {len(db_rows)} 条")
    return total


def sync_market_summary(conn: sqlite3.Connection) -> int:
    rows = fetch_market_summary()
    today = date.today().isoformat()
    for r in rows:
        if not r["trade_date"]:
            r["trade_date"] = today
        elif len(r["trade_date"]) == 8 and r["trade_date"].isdigit():
            d = r["trade_date"]
            r["trade_date"] = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    if rows:
        conn.executemany(
            """INSERT OR REPLACE INTO market_summary_daily
               (trade_date, exchange, category, count, turnover, total_mv, circ_mv, pe_avg)
               VALUES (:trade_date, :exchange, :category, :count, :turnover, :total_mv, :circ_mv, :pe_avg)""",
            rows,
        )
        conn.commit()
    return len(rows)


def main() -> None:
    conn = sqlite3.connect(config.DB_PATH)
    code_map = _code_map(conn)

    n1 = sync_new_high_low(conn)
    print(f"[创新高新低] 共写入 {n1} 条")

    n2 = sync_lhb_period_stats(conn, code_map)
    print(f"[龙虎榜多周期] 共写入 {n2} 条")

    n3 = sync_market_summary(conn)
    print(f"[市场总貌] 共写入 {n3} 条")

    conn.close()


if __name__ == "__main__":
    main()
