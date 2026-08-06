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
        active_n = len(stocks)

        # 增量:跳过已充分覆盖的交易日。此前每天把最近 ~90 天×5292 只全量重算,
        # 单步耗时 3h42m 且持写锁(WAL 涨到 1.2GB),而其中 99% 是昨天已算过的。
        # 用 F013(每只活跃股必写)的当日行数作覆盖度代理,达 90% 即视为已覆盖。
        threshold = int(active_n * 0.9)
        covered = dict(
            conn.execute(
                "SELECT date, COUNT(*) FROM factor_values "
                "WHERE factor_id='F013' AND date>=? GROUP BY date",
                (trade_dates[0],),
            ).fetchall()
        ) if trade_dates else {}

        tech_count = 0
        computed_days = 0
        skipped_days = 0
        for dt in trade_dates:
            if covered.get(dt, 0) >= threshold:
                skipped_days += 1
                continue
            for (sid,) in stocks:
                tech_count += _compute_technical_factors(conn, sid, dt)
            # 每个交易日(全市场慢计算+写入)提交一次，避免跨日长事务持有写锁
            conn.commit()
            computed_days += 1
            # 算完一天主动 checkpoint,别让 WAL 在多日回填里堆起来(能收多少收多少)
            try:
                conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            except sqlite3.OperationalError:
                pass

        score_count = _backfill_score_factors(conn)
        conn.commit()

        factor_days = conn.execute("SELECT COUNT(DISTINCT date) FROM factor_values").fetchone()[0]
        conn.close()
    finally:
        config.DB_PATH = prev

    return {
        "trade_dates_processed": len(trade_dates),
        "computed_days": computed_days,
        "skipped_days": skipped_days,
        "technical_writes": tech_count,
        "score_backfill_writes": score_count,
        "factor_history_days": factor_days,
        "target_days": 60,
        "merge_ready": factor_days >= 60,
    }
