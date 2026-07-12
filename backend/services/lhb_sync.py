"""龙虎榜历史入库 — 东财 datacenter 批量/按日拉取。"""
from __future__ import annotations

import sqlite3
import time
from datetime import date, timedelta

from config import DB_PATH
from services.lhb_fetch import _fetch_daily_eastmoney, _list_dates_eastmoney, _norm_code


def ensure_lhb_table(conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS lhb_daily (
            stock_id INTEGER NOT NULL,
            trade_date TEXT NOT NULL,
            code TEXT NOT NULL,
            net_buy REAL,
            buy_amount REAL,
            sell_amount REAL,
            deal_amount REAL,
            change_pct REAL,
            turnover_pct REAL,
            reason TEXT DEFAULT '',
            source TEXT DEFAULT 'eastmoney',
            PRIMARY KEY (stock_id, trade_date))"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_lhb_daily_date ON lhb_daily(trade_date DESC)"
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS lhb_market_daily (
            trade_date TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            close REAL,
            net_buy REAL,
            buy_amount REAL,
            sell_amount REAL,
            deal_amount REAL,
            change_pct REAL,
            turnover_pct REAL,
            reason TEXT DEFAULT '',
            source TEXT DEFAULT 'eastmoney',
            synced_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (trade_date, code))"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_lhb_market_daily_date ON lhb_market_daily(trade_date DESC)"
    )
    conn.commit()
    if own:
        conn.close()


def load_lhb_market_from_db(trade_date: str) -> list[dict]:
    """读取已入库的全市场龙虎榜（按净买排序）。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_lhb_table(conn)
    rows = conn.execute(
        """SELECT trade_date AS date, code, name, close, change_pct, turnover_pct,
                  net_buy, buy_amount, sell_amount, deal_amount, reason, source
           FROM lhb_market_daily WHERE trade_date=?
           ORDER BY ABS(COALESCE(net_buy, 0)) DESC""",
        (trade_date,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_lhb_market_to_db(trade_date: str, items: list[dict], *, source: str = "eastmoney") -> int:
    """全市场龙虎榜落库，并同步跟踪池 lhb_daily。"""
    if not items:
        return 0
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_lhb_table(conn)
    code_to_id = {
        r["code"]: int(r["id"])
        for r in conn.execute("SELECT id, code FROM stocks WHERE is_active=1").fetchall()
    }
    written = 0
    for item in items:
        code = item.get("code")
        if not code:
            continue
        td = item.get("date") or trade_date
        conn.execute(
            """INSERT OR REPLACE INTO lhb_market_daily
            (trade_date, code, name, close, net_buy, buy_amount, sell_amount, deal_amount,
             change_pct, turnover_pct, reason, source, synced_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
            (
                td,
                code,
                item.get("name"),
                item.get("close"),
                item.get("net_buy"),
                item.get("buy_amount"),
                item.get("sell_amount"),
                item.get("deal_amount"),
                item.get("change_pct"),
                item.get("turnover_pct"),
                (item.get("reason") or "")[:120],
                item.get("source") or source,
            ),
        )
        written += 1
        sid = code_to_id.get(code)
        if sid:
            item["source"] = item.get("source") or source
            _upsert_lhb_row(conn, sid, item)
    conn.commit()
    conn.close()
    return written


def sync_lhb_market_for_date(trade_date: str) -> dict:
    """拉取并入库指定交易日全市场龙虎榜。"""
    items = _fetch_daily_eastmoney(trade_date)
    if not items:
        return {"date": trade_date, "rows_written": 0, "error": "no_data"}
    n = save_lhb_market_to_db(trade_date, items, source="eastmoney")
    return {"date": trade_date, "rows_written": n, "count": len(items)}


def sync_lhb_latest_market_day(*, lookback: int = 5) -> dict:
    """从最近若干自然日中找到最新有榜日并入库。"""
    today = date.today()
    for i in range(lookback):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        existing = load_lhb_market_from_db(d)
        if existing:
            return {"date": d, "rows_written": 0, "count": len(existing), "from_db": True}
        out = sync_lhb_market_for_date(d)
        if out.get("rows_written", 0) > 0:
            return out
    return {"error": "no_lhb_in_lookback", "lookback": lookback}


def _upsert_lhb_row(conn: sqlite3.Connection, stock_id: int, item: dict) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO lhb_daily
        (stock_id, trade_date, code, net_buy, buy_amount, sell_amount, deal_amount,
         change_pct, turnover_pct, reason, source)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            stock_id,
            item.get("date"),
            item.get("code"),
            item.get("net_buy"),
            item.get("buy_amount"),
            item.get("sell_amount"),
            item.get("deal_amount"),
            item.get("change_pct"),
            item.get("turnover_pct"),
            (item.get("reason") or "")[:120],
            item.get("source") or "eastmoney",
        ),
    )


def sync_lhb_market_days(
    *,
    days: int = 60,
    sleep_ms: int = 200,
) -> dict:
    """按日拉全市场龙虎榜并入库（仅跟踪池股票）。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_lhb_table(conn)
    code_to_id = {
        r["code"]: int(r["id"])
        for r in conn.execute("SELECT id, code FROM stocks WHERE is_active=1").fetchall()
    }
    rows_written = days_hit = 0
    errors: list[str] = []
    today = date.today()
    for i in range(days):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        try:
            items = _fetch_daily_eastmoney(d)
            if not items:
                continue
            days_hit += 1
            for item in items:
                sid = code_to_id.get(item.get("code"))
                if not sid:
                    continue
                item["source"] = "eastmoney"
                _upsert_lhb_row(conn, sid, item)
                rows_written += 1
        except Exception as e:
            errors.append(f"{d}:{e}")
        if sleep_ms > 0:
            time.sleep(sleep_ms / 1000)
    conn.commit()
    conn.close()
    return {
        "days_scanned": days,
        "days_with_data": days_hit,
        "rows_written": rows_written,
        "errors": errors[:10],
    }


def sync_lhb_watchlist(
    stock_ids: list[int] | None = None,
    *,
    max_dates_per_stock: int = 80,
    sleep_ms: int = 150,
) -> dict:
    """跟踪池个股历史上榜记录（按股拉日期列表）。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_lhb_table(conn)
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
        sid, code = int(row["id"]), _norm_code(row["code"])
        try:
            dates = _list_dates_eastmoney(code, max_dates_per_stock)
            if not dates:
                continue
            for d in dates:
                for item in _fetch_daily_eastmoney(d):
                    if item.get("code") != code:
                        continue
                    item["source"] = "eastmoney"
                    _upsert_lhb_row(conn, sid, item)
                    rows_written += 1
                    break
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
    }


def backfill_lhb_history(*, years: int = 2, sleep_ms: int = 250) -> dict:
    """近 N 年市场按日扫描 + 跟踪池按股补全。"""
    calendar_days = min(365 * years, 730)
    market = sync_lhb_market_days(days=calendar_days, sleep_ms=sleep_ms)
    watch = sync_lhb_watchlist(max_dates_per_stock=120, sleep_ms=sleep_ms)
    return {"market": market, "watchlist": watch, "years": years}
