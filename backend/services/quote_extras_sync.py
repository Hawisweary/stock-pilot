"""补全 stock_daily_quotes 成交额/换手率/涨跌幅（东财/akshare，不覆盖 OHLCV）。"""
from __future__ import annotations

import sqlite3
import time

from config import DB_PATH
from services.eastmoney_adapter import fetch_daily_extras


def _enrich_from_tencent_realtime(
    stock_id: int,
    code: str,
    conn: sqlite3.Connection,
) -> int:
    """腾讯实时补当日成交额/换手率（push2his 不可用时的兜底）。"""
    from services.data_sources import tencent_quote

    q = tencent_quote([code]).get(code)
    if not q:
        return 0
    row = conn.execute(
        """SELECT trade_date FROM stock_daily_quotes
           WHERE stock_id=? AND close IS NOT NULL
           ORDER BY trade_date DESC LIMIT 1""",
        (stock_id,),
    ).fetchone()
    if not row:
        return 0
    amount = (q.get("amount_wan") or 0) * 10000
    turnover = q.get("turnover_pct")
    change_pct = q.get("change_pct")
    sets, vals = [], []
    if amount:
        sets.append("amount=?")
        vals.append(amount)
    if turnover is not None:
        sets.append("turnover=?")
        vals.append(turnover)
    if change_pct is not None:
        sets.append("change_pct=?")
        vals.append(change_pct)
    if not sets:
        return 0
    vals.extend([stock_id, row[0]])
    cur = conn.execute(
        f"""UPDATE stock_daily_quotes SET {", ".join(sets)}
            WHERE stock_id=? AND trade_date=?""",
        vals,
    )
    return cur.rowcount


def enrich_stock_quote_extras(
    stock_id: int,
    code: str,
    *,
    max_bars: int = 500,
    conn: sqlite3.Connection | None = None,
) -> int:
    extras = fetch_daily_extras(code, count=max_bars)

    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    updated = 0
    try:
        for dt, fields in extras.items():
            sets = []
            vals: list = []
            for col in ("amount", "turnover", "change_pct"):
                v = fields.get(col)
                if v is not None:
                    sets.append(f"{col}=?")
                    vals.append(v)
            if not sets:
                continue
            vals.extend([stock_id, dt])
            cur = conn.execute(
                f"""UPDATE stock_daily_quotes SET {", ".join(sets)}
                    WHERE stock_id=? AND trade_date=?""",
                vals,
            )
            if cur.rowcount:
                updated += cur.rowcount
        if updated == 0:
            updated += _enrich_from_tencent_realtime(stock_id, code, conn)
        if own:
            conn.commit()
    finally:
        if own:
            conn.close()
    return updated


def enrich_active_quote_extras(
    *,
    max_bars: int = 500,
    sleep_ms: int = 150,
) -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, code FROM stocks WHERE is_active=1 ORDER BY id"
    ).fetchall()
    ok = err = cells = 0
    errors: list[str] = []
    for r in rows:
        sid, code = int(r["id"]), r["code"]
        try:
            n = enrich_stock_quote_extras(sid, code, max_bars=max_bars, conn=conn)
            conn.commit()
            cells += n
            ok += 1 if n else 0
            if n == 0:
                err += 1
        except Exception as e:
            err += 1
            if len(errors) < 5:
                errors.append(f"{code}:{e}")
        if sleep_ms > 0:
            time.sleep(sleep_ms / 1000)
    conn.close()
    return {
        "stocks": len(rows),
        "synced": ok,
        "failed": err,
        "cells_updated": cells,
        "errors": errors,
    }
