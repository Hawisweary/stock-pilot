"""除权除息同步与前复权 adj_close 计算 — 东财 RPT_SHAREBONUS_DET。"""
from __future__ import annotations

import sqlite3
from typing import Any, Optional

from config import DB_PATH
from services.data_processor import normalize_code
from services.data_sources import _eastmoney_datacenter
from services.data_cleaner import ensure_quote_columns


def ensure_ex_rights_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS stock_ex_rights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL REFERENCES stocks(id),
            ex_date TEXT NOT NULL,
            cash_div REAL DEFAULT 0,
            bonus_ratio REAL DEFAULT 0,
            transfer_ratio REAL DEFAULT 0,
            plan_notice_date TEXT DEFAULT '',
            source TEXT DEFAULT 'eastmoney',
            UNIQUE(stock_id, ex_date)
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ex_rights_stock ON stock_ex_rights(stock_id, ex_date)"
    )


def fetch_ex_rights_events(code: str, page_size: int = 200) -> list[dict]:
    """东财除权除息事件列表。"""
    code = normalize_code(code)
    rows = _eastmoney_datacenter(
        "RPT_SHAREBONUS_DET",
        filter_str=f'(SECURITY_CODE="{code}")',
        page_size=page_size,
        sort_columns="EX_DIVIDEND_DATE",
        sort_types="-1",
    )
    events: list[dict] = []
    for row in rows:
        ex_date = str(row.get("EX_DIVIDEND_DATE") or "")[:10]
        if not ex_date or ex_date == "None":
            continue
        pretax = _to_float(row.get("PRETAX_BONUS_RMB"))
        cash_div = pretax / 10.0 if pretax else 0.0
        bonus = _to_float(row.get("BONUS_RATIO")) or 0.0
        transfer = _to_float(row.get("IT_RATIO")) or 0.0
        events.append(
            {
                "ex_date": ex_date,
                "cash_div": cash_div,
                "bonus_ratio": bonus,
                "transfer_ratio": transfer,
                "plan_notice_date": str(row.get("PLAN_NOTICE_DATE") or "")[:10],
            }
        )
    events.sort(key=lambda x: x["ex_date"])
    return events


def sync_ex_rights(stock_id: int, code: str, conn: Optional[sqlite3.Connection] = None) -> int:
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    ensure_ex_rights_table(conn)
    events = fetch_ex_rights_events(code)
    n = 0
    for ev in events:
        conn.execute(
            """INSERT INTO stock_ex_rights
               (stock_id, ex_date, cash_div, bonus_ratio, transfer_ratio, plan_notice_date, source)
               VALUES (?,?,?,?,?,?, 'eastmoney')
               ON CONFLICT(stock_id, ex_date) DO UPDATE SET
                 cash_div=excluded.cash_div,
                 bonus_ratio=excluded.bonus_ratio,
                 transfer_ratio=excluded.transfer_ratio,
                 plan_notice_date=excluded.plan_notice_date""",
            (
                stock_id,
                ev["ex_date"],
                ev["cash_div"],
                ev["bonus_ratio"],
                ev["transfer_ratio"],
                ev.get("plan_notice_date", ""),
            ),
        )
        n += 1
    if own:
        conn.commit()
        conn.close()
    return n


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _close_on_or_before(conn: sqlite3.Connection, stock_id: int, date: str) -> float | None:
    row = conn.execute(
        """SELECT close FROM stock_daily_quotes
           WHERE stock_id=? AND trade_date <= ? AND close IS NOT NULL
           ORDER BY trade_date DESC LIMIT 1""",
        (stock_id, date),
    ).fetchone()
    return float(row[0]) if row and row[0] else None


def apply_forward_adj(
    stock_id: int,
    *,
    quote_source: str = "qfq",
    conn: Optional[sqlite3.Connection] = None,
) -> dict:
    """
    写入 adj_close。
    - quote_source=qfq（腾讯）：close 已是前复权，adj_close=close
    - quote_source=raw（yfinance 等）：按除权事件自算前复权
    """
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    ensure_quote_columns(conn)
    ensure_ex_rights_table(conn)

    if quote_source == "qfq":
        updated = conn.execute(
            """UPDATE stock_daily_quotes
               SET adj_close = close
               WHERE stock_id=? AND close IS NOT NULL""",
            (stock_id,),
        ).rowcount
        if own:
            conn.commit()
            conn.close()
        return {"mode": "qfq_passthrough", "updated": updated}

    events = conn.execute(
        """SELECT ex_date, cash_div, bonus_ratio, transfer_ratio
           FROM stock_ex_rights WHERE stock_id=? ORDER BY ex_date DESC""",
        (stock_id,),
    ).fetchall()

    factor = 1.0
    factor_by_date: dict[str, float] = {}
    for ex_date, cash, bonus, transfer in events:
        ex_date = str(ex_date)[:10]
        prev_close = _close_on_or_before(conn, stock_id, ex_date)
        if prev_close and prev_close > 0:
            share_mult = 10.0 / (10.0 + float(bonus or 0) + float(transfer or 0))
            cash_factor = (prev_close - float(cash or 0)) / prev_close
            factor *= max(cash_factor, 0.0001) * share_mult
        factor_by_date[ex_date] = factor

    quotes = conn.execute(
        """SELECT trade_date, open, high, low, close FROM stock_daily_quotes
           WHERE stock_id=? AND close IS NOT NULL ORDER BY trade_date""",
        (stock_id,),
    ).fetchall()

    ex_sorted = sorted(factor_by_date.items(), key=lambda x: x[0], reverse=True)
    updated = 0
    for trade_date, o, h, l, c in quotes:
        f = 1.0
        for ex_date, fv in ex_sorted:
            if str(trade_date) < ex_date:
                f = fv
                break
        adj = float(c) * f
        conn.execute(
            """UPDATE stock_daily_quotes SET adj_close=?
               WHERE stock_id=? AND trade_date=?""",
            (adj, stock_id, trade_date),
        )
        updated += 1

    if own:
        conn.commit()
        conn.close()
    return {"mode": "computed", "events": len(events), "updated": updated}
