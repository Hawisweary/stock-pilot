"""扩展 factor_values 历史 — 按交易日回填技术面因子 F009-F014"""
from __future__ import annotations

import sqlite3

import config
from config import DB_PATH
from services.factor_factory import init_factor_store, _compute_technical_factors, _backfill_score_factors


def expand_factor_history(days: int = 90, db_path: str = None) -> dict:
    path = db_path or DB_PATH
    prev = config.DB_PATH
    config.DB_PATH = path
    try:
        conn = init_factor_store()
        trade_dates = [
            r[0]
            for r in conn.execute(
                """SELECT DISTINCT trade_date FROM stock_daily_quotes
                   WHERE close IS NOT NULL ORDER BY trade_date DESC LIMIT ?""",
                (days,),
            ).fetchall()
        ]
        trade_dates = sorted(trade_dates)

        stocks = conn.execute("SELECT id FROM stocks WHERE is_active=1").fetchall()
        tech_count = 0
        for dt in trade_dates:
            for (sid,) in stocks:
                tech_count += _compute_technical_factors(conn, sid, dt)

        score_count = _backfill_score_factors(conn)
        conn.commit()

        factor_days = conn.execute("SELECT COUNT(DISTINCT date) FROM factor_values").fetchone()[0]
        conn.close()
    finally:
        config.DB_PATH = prev

    return {
        "trade_dates_processed": len(trade_dates),
        "technical_writes": tech_count,
        "score_backfill_writes": score_count,
        "factor_history_days": factor_days,
        "target_days": 60,
        "merge_ready": factor_days >= 60,
    }
