"""融资融券余额同步 — 东财 datacenter RPTA_WEB_RZRQ_GGMX。"""
from __future__ import annotations

import sqlite3
import time

from config import DB_PATH
from services.data_sources import margin_trading


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS eastmoney_margin (
            stock_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            margin_balance REAL,
            margin_buy REAL,
            PRIMARY KEY (stock_id, date))"""
    )


def sync_margin_balance(
    stock_ids: list[int] | None = None,
    *,
    page_size: int = 60,
    sleep_ms: int = 120,
) -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    _ensure_table(conn)
    if stock_ids:
        ph = ",".join("?" * len(stock_ids))
        stocks = conn.execute(
            f"SELECT id, code FROM stocks WHERE id IN ({ph}) AND is_active=1",
            stock_ids,
        ).fetchall()
    else:
        stocks = conn.execute(
            "SELECT id, code FROM stocks WHERE is_active=1 ORDER BY id"
        ).fetchall()

    synced = rows_written = 0
    errors: list[str] = []
    for row in stocks:
        sid, code = int(row["id"]), row["code"]
        try:
            hist = margin_trading(code, page_size=page_size)
            if not hist:
                continue
            for item in hist:
                conn.execute(
                    """INSERT OR REPLACE INTO eastmoney_margin
                    (stock_id, date, margin_balance, margin_buy)
                    VALUES (?,?,?,?)""",
                    (
                        sid,
                        item.get("date"),
                        float(item.get("rzye") or 0),
                        float(item.get("rzmre") or 0),
                    ),
                )
                rows_written += 1
            synced += 1
        except Exception as e:
            errors.append(f"{code}:{e}")
        if sleep_ms > 0:
            time.sleep(sleep_ms / 1000)
    conn.commit()
    conn.close()
    return {
        "synced": synced,
        "total": len(stocks),
        "rows_written": rows_written,
        "errors": errors[:10],
        "source": "eastmoney_datacenter",
    }
