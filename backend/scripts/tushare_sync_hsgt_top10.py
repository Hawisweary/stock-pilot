"""同步沪深股通十大成交股（hsgt_top10）到本地 hsgt_top10_daily 表。

每日 18~20 点更新，建议每天收盘后跑一次，默认回补最近 N 个交易日。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from services.tushare_adapter import fetch_hsgt_top10


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=10, help="回补最近 N 个交易日（默认10）")
    args = parser.parse_args()

    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=int(args.days * 1.6) + 10)).strftime("%Y%m%d")
    trading_days = _trading_days(start, end)[-args.days:]
    print(f"沪深股通十大成交股: {len(trading_days)} 个交易日 ({trading_days[0]}~{trading_days[-1]})")

    conn = sqlite3.connect(config.DB_PATH)
    code_map = _code_map(conn)

    total = 0
    for i, d in enumerate(trading_days, 1):
        rows = fetch_hsgt_top10(d)
        trade_date_fmt = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        db_rows = []
        for r in rows:
            code = r["ts_code"].split(".")[0]
            stock_id = code_map.get(code)
            if stock_id is None:
                continue
            db_rows.append((
                stock_id, trade_date_fmt, r["market_type"], r["name"], r["close"],
                r["change"], r["rank"], r["amount"], r["net_amount"], r["buy"], r["sell"],
            ))
        if db_rows:
            conn.executemany(
                """INSERT OR REPLACE INTO hsgt_top10_daily
                   (stock_id, trade_date, market_type, name, close, change, rank,
                    amount, net_amount, buy, sell)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                db_rows,
            )
            conn.commit()
            total += len(db_rows)
        print(f"  [{i}/{len(trading_days)}] {d}: {len(db_rows)} 条")

    conn.close()
    print(f"完成，共写入 {total} 条")


if __name__ == "__main__":
    main()
