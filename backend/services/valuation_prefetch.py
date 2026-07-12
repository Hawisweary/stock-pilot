"""估值快照批量预取 — 腾讯行情 → valuation_snapshots"""
from __future__ import annotations

import sqlite3
from datetime import date

import config


def prefetch_valuation_snapshots(stock_ids: list[int] | None = None) -> dict:
    """为活跃股票批量写入 valuation_snapshots（PE/PB/市值）。"""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        if stock_ids:
            ph = ",".join(["?"] * len(stock_ids))
            rows = conn.execute(
                f"SELECT id, code FROM stocks WHERE is_active=1 AND id IN ({ph}) ORDER BY id",
                tuple(stock_ids),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, code FROM stocks WHERE is_active=1 ORDER BY id"
            ).fetchall()
        if not rows:
            return {"updated": 0, "total": 0}

        from services.data_sources import tencent_quote

        CHUNK = 200
        all_codes = [str(r["code"]) for r in rows]
        quotes: dict = {}
        for i in range(0, len(all_codes), CHUNK):
            chunk_quotes = tencent_quote(all_codes[i:i + CHUNK])
            quotes.update(chunk_quotes)

        as_of = date.today().strftime("%Y-%m-%d")
        updated = 0
        for r in rows:
            q = quotes.get(str(r["code"]), {})
            pe = q.get("pe_ttm")
            pb = q.get("pb")
            if pe is None and pb is None:
                continue
            conn.execute(
                """INSERT OR REPLACE INTO valuation_snapshots
                   (stock_id, pe_ttm, pb, market_cap, dividend_yield, as_of_date)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    int(r["id"]),
                    pe,
                    pb,
                    q.get("market_cap") or q.get("mcap_yi"),
                    q.get("dividend_yield"),
                    as_of,
                ),
            )
            updated += 1
        conn.commit()
        return {"updated": updated, "total": len(rows), "as_of_date": as_of}
    finally:
        conn.close()
