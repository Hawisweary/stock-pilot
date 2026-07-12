"""同步 SSE 交易日历到本地 trade_calendar 表（含法定节假日 + 调休补班）。

覆盖范围较宽（默认前后各 2 年）一次性拉满，之后每年年初重跑一次补齐新年份即可,
交易所很少对已公布日历做回溯修改。
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from services.tushare_adapter import fetch_trade_calendar


def main() -> None:
    today = date.today()
    start = f"{today.year - 2}0101"
    end = f"{today.year + 2}1231"

    rows = fetch_trade_calendar(start, end)
    print(f"[交易日历] 拉取到 {len(rows)} 条记录 ({start} ~ {end})")

    conn = sqlite3.connect(config.DB_PATH)
    conn.executemany(
        "INSERT OR REPLACE INTO trade_calendar (cal_date, is_open) VALUES (?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    print("[交易日历] 已写入 trade_calendar")


if __name__ == "__main__":
    main()
