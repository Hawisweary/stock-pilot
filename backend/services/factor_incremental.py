"""因子增量计算 — 仅更新有新行情/评分的股票与日期"""
from __future__ import annotations

import sqlite3
import time
from datetime import date
from typing import List, Optional, Set

from config import DB_PATH


def ensure_log_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS factor_compute_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mode TEXT NOT NULL,
            target_date TEXT,
            stocks_touched INTEGER DEFAULT 0,
            cells_written INTEGER DEFAULT 0,
            duration_ms INTEGER,
            detail_json TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)


def _latest_trade_date(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute(
        "SELECT MAX(trade_date) FROM stock_daily_quotes WHERE close IS NOT NULL"
    ).fetchone()
    return row[0] if row and row[0] else None


def _stocks_with_quote_on(conn: sqlite3.Connection, trade_date: str) -> List[int]:
    return [
        r[0]
        for r in conn.execute(
            """SELECT DISTINCT stock_id FROM stock_daily_quotes
               WHERE trade_date=? AND COALESCE(adj_close, close) IS NOT NULL""",
            (trade_date,),
        ).fetchall()
    ]


def _stocks_with_new_quotes_since(conn: sqlite3.Connection, since_date: str) -> Set[int]:
    rows = conn.execute(
        """SELECT DISTINCT stock_id FROM stock_daily_quotes
           WHERE trade_date > ? AND COALESCE(adj_close, close) IS NOT NULL""",
        (since_date,),
    ).fetchall()
    return {r[0] for r in rows}


def _last_incremental_date(conn: sqlite3.Connection) -> Optional[str]:
    ensure_log_table(conn)
    row = conn.execute(
        """SELECT target_date FROM factor_compute_log
           WHERE mode IN ('incremental', 'full')
           ORDER BY created_at DESC LIMIT 1"""
    ).fetchone()
    return row[0] if row else None


def _sync_wide_for_stocks(conn: sqlite3.Connection, calc_date: str, stock_ids: List[int]) -> int:
    from services.factor_values_wide import FACTOR_ID_TO_COL, upsert_wide_row

    if not stock_ids:
        return 0
    ph = ",".join("?" * len(stock_ids))
    rows = conn.execute(
        f"""SELECT stock_id, factor_id, value FROM factor_values
            WHERE date=? AND stock_id IN ({ph}) AND value IS NOT NULL""",
        (calc_date, *stock_ids),
    ).fetchall()
    batch: dict[tuple[int, str], dict] = {}
    for sid, fid, val in rows:
        key = (sid, calc_date)
        batch.setdefault(key, {})[fid] = float(val)
    for (sid, dt), facs in batch.items():
        upsert_wide_row(conn, sid, dt, facs)
    return len(rows)


def _update_ranks_for_date(conn: sqlite3.Connection, calc_date: str, factor_ids: Optional[List[str]] = None) -> None:
    if factor_ids:
        fids = factor_ids
    else:
        fids = [r[0] for r in conn.execute(
            "SELECT DISTINCT factor_id FROM factor_values WHERE date=?", (calc_date,)
        ).fetchall()]
    for fid in fids:
        vals = conn.execute(
            "SELECT stock_id, value FROM factor_values WHERE factor_id=? AND date=? ORDER BY value DESC",
            (fid, calc_date),
        ).fetchall()
        for rank, (sid, _) in enumerate(vals, 1):
            conn.execute(
                "UPDATE factor_values SET rank=? WHERE stock_id=? AND date=? AND factor_id=?",
                (rank, sid, calc_date, fid),
            )


def compute_factors_incremental(stock_ids: Optional[List[int]] = None) -> dict:
    """
    增量更新：
    - 技术因子 F009-F014：最新交易日、有行情的股票
    - 评分因子 F001-F008：最新 comprehensive calc_date
    - F015：最新 debate 日
    不做历史 backfill。
    """
    t0 = time.perf_counter()
    from services.factor_factory import (
        _compute_technical_factors,
        _upsert_factor,
        init_factor_store,
    )
    from services.factor_quality import filter_fundamental_for_backfill, is_factor_value_valid

    conn = init_factor_store()
    ensure_log_table(conn)
    from services.financial_calendar import ensure_tables

    ensure_tables(conn)
    conn.commit()

    target_date = _latest_trade_date(conn)
    if not target_date:
        conn.close()
        return {"error": "无行情数据", "mode": "incremental"}

    last = _last_incremental_date(conn)
    quote_stocks = set(_stocks_with_quote_on(conn, target_date))
    if last:
        quote_stocks |= _stocks_with_new_quotes_since(conn, last)

    if stock_ids:
        touch = set(stock_ids) & quote_stocks
    else:
        active = {
            r[0]
            for r in conn.execute("SELECT id FROM stocks WHERE is_active=1").fetchall()
        }
        touch = quote_stocks & active

    touch_list = sorted(touch)
    cells = 0
    fid_map = ["F001", "F002", "F003", "F004", "F005", "F006", "F007", "F008"]

    latest_cs = conn.execute("SELECT MAX(calc_date) FROM comprehensive_scores").fetchone()[0]
    if latest_cs:
        cs_rows = {
            r[0]: r
            for r in conn.execute(
                """SELECT stock_id, composite_score, fundamental_score, technical_score,
                          sentiment_score, capital_score, policy_score, mood_score, val_score
                   FROM comprehensive_scores WHERE calc_date=?""",
                (latest_cs,),
            ).fetchall()
        }
        for sid in touch_list:
            if sid not in cs_rows:
                continue
            vals = cs_rows[sid]
            dt = latest_cs if latest_cs <= target_date else target_date
            for i, fid in enumerate(fid_map):
                if vals[i] is None:
                    continue
                v = float(vals[i])
                qf = None
                if fid == "F002":
                    v2 = filter_fundamental_for_backfill(sid, dt, v)
                    if v2 is None:
                        continue
                    v = v2
                    _, qf = is_factor_value_valid("F002", sid, dt)
                _upsert_factor(conn, sid, dt, fid, v, qf)
                cells += 1

    code_map: dict[int, str] = {}
    if touch_list:
        code_map = {
            r[0]: r[1]
            for r in conn.execute(
                f"""SELECT id, code FROM stocks WHERE id IN ({",".join("?" * len(touch_list))})""",
                touch_list,
            ).fetchall()
        }
    for sid in touch_list:
        cells += _compute_technical_factors(
            conn, sid, target_date, code=code_map.get(sid, "")
        )

    try:
        deb_date = conn.execute("SELECT MAX(date) FROM debate_v2").fetchone()[0]
        if deb_date:
            for sid, score in conn.execute(
                "SELECT stock_id, adjusted_score FROM debate_v2 WHERE date=?", (deb_date,)
            ):
                if sid in touch or not stock_ids:
                    _upsert_factor(conn, sid, deb_date, "F015", float(score))
                    cells += 1
    except sqlite3.OperationalError:
        pass

    _update_ranks_for_date(conn, target_date)
    if latest_cs and latest_cs != target_date:
        _update_ranks_for_date(conn, latest_cs)

    neutral_writes = 0
    try:
        from services.factor_neutralize import neutralize_factor
        from services.ohlcv_technical_factors import NEUTRALIZE_SOURCE_IDS

        for fid in NEUTRALIZE_SOURCE_IDS:
            r = neutralize_factor(fid, max_dates=1)
            neutral_writes += int(r.get("cells_written") or 0)
    except Exception:
        pass

    wide_cells = _sync_wide_for_stocks(conn, target_date, touch_list)
    if latest_cs:
        wide_cells += _sync_wide_for_stocks(conn, latest_cs, touch_list)

    duration_ms = round((time.perf_counter() - t0) * 1000)
    conn.execute(
        """INSERT INTO factor_compute_log (mode, target_date, stocks_touched, cells_written, duration_ms)
           VALUES ('incremental', ?, ?, ?, ?)""",
        (target_date, len(touch_list), cells, duration_ms),
    )
    conn.commit()
    conn.close()

    return {
        "mode": "incremental",
        "target_date": target_date,
        "stocks_touched": len(touch_list),
        "cells_written": cells,
        "wide_cells_synced": wide_cells,
        "duration_ms": duration_ms,
        "comprehensive_date": latest_cs,
        "neutral_cells_written": neutral_writes,
    }
