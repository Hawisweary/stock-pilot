"""同步同花顺(THS)+东方财富(DC)概念板块到 stock_concept_boards 表。

两套概念分类体系独立、不互通，用 source 列区分：
- THS: 低频批量，板块本身稳定，成分股变动慢 -> 手动/低频运行即可，每次全量覆盖 source='ths' 的记录
- DC:  按日快照，板块和成分股每天可能变化 -> 建议每日运行一次，每次全量覆盖 source='dc' 的记录
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from services.tushare_adapter import (
    fetch_dc_concept_boards,
    fetch_dc_concept_members,
    fetch_ths_concept_boards,
    fetch_ths_concept_members,
)


def _code_map(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT id, code FROM stocks").fetchall()
    return {code: sid for sid, code in rows}


def _strip_suffix(ts_code: str) -> str:
    return ts_code.split(".")[0]


def sync_ths(conn: sqlite3.Connection, code_map: dict[str, int]) -> None:
    today = date.today().isoformat()
    boards = fetch_ths_concept_boards()
    print(f"[THS] {len(boards)} 个概念板块")

    rows: list[tuple[int, str, str, str]] = []
    for i, b in enumerate(boards, 1):
        members = fetch_ths_concept_members(b["ts_code"])
        matched = 0
        for con_code in members:
            code = _strip_suffix(con_code)
            stock_id = code_map.get(code)
            if stock_id is None:
                continue
            rows.append((stock_id, b["ts_code"], b["name"]))
            matched += 1
        if i % 50 == 0:
            print(f"[THS] {i}/{len(boards)} 板块已处理")

    conn.execute("DELETE FROM stock_concept_boards WHERE source='ths'")
    conn.executemany(
        """INSERT OR REPLACE INTO stock_concept_boards
           (stock_id, bk_code, name, type, fetched_date, source)
           VALUES (?, ?, ?, '概念', ?, 'ths')""",
        [(sid, bk, name, today) for sid, bk, name in rows],
    )
    conn.commit()
    print(f"[THS] 写入 {len(rows)} 条成分股记录")


def sync_dc(conn: sqlite3.Connection, code_map: dict[str, int], trade_date: str) -> None:
    boards = fetch_dc_concept_boards(trade_date)
    print(f"[DC] {len(boards)} 个概念板块 (交易日 {trade_date})")

    rows: list[tuple[int, str, str]] = []
    for i, b in enumerate(boards, 1):
        members = fetch_dc_concept_members(b["ts_code"])
        for con_code in members:
            code = _strip_suffix(con_code)
            stock_id = code_map.get(code)
            if stock_id is None:
                continue
            rows.append((stock_id, b["ts_code"], b["name"]))
        if i % 50 == 0:
            print(f"[DC] {i}/{len(boards)} 板块已处理")

    fetched_date = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
    conn.execute("DELETE FROM stock_concept_boards WHERE source='dc'")
    conn.executemany(
        """INSERT OR REPLACE INTO stock_concept_boards
           (stock_id, bk_code, name, type, fetched_date, source)
           VALUES (?, ?, ?, '概念', ?, 'dc')""",
        [(sid, bk, name, fetched_date) for sid, bk, name in rows],
    )
    conn.commit()
    print(f"[DC] 写入 {len(rows)} 条成分股记录")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-ths", action="store_true")
    parser.add_argument("--skip-dc", action="store_true")
    parser.add_argument("--trade-date", default=None, help="DC 快照交易日 YYYYMMDD，默认今天")
    args = parser.parse_args()

    conn = sqlite3.connect(config.DB_PATH)
    code_map = _code_map(conn)

    if not args.skip_ths:
        sync_ths(conn, code_map)
    if not args.skip_dc:
        from services.tushare_adapter import latest_trading_date

        trade_date = args.trade_date or latest_trading_date(date.today().strftime("%Y%m%d"))
        sync_dc(conn, code_map, trade_date)

    conn.close()


if __name__ == "__main__":
    main()
