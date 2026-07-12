"""扩展 comprehensive_scores / F001-F008 历史 — 从维度表 + factor_scores 合成"""
from __future__ import annotations

import json
import sqlite3

import config
from config import DB_PATH, DEFAULT_SCORE
from services.factor_factory import _backfill_score_factors, _upsert_factor


def _forward_fill_score_factors(conn: sqlite3.Connection, trade_dates: list[str]) -> int:
    """按交易日 forward-fill F001-F008（来自 factor_scores as-of）"""
    fid_map = [
        ("composite_score", "F001"),
        ("profitability_score", "F002"),
        ("momentum_score", "F003"),
        ("value_score", "F008"),
    ]
    col_names = [c for c, _ in fid_map]
    stocks = conn.execute("SELECT id FROM stocks WHERE is_active=1").fetchall()
    count = 0
    for (sid,) in stocks:
        last: dict[str, float] = {}
        for dt in trade_dates:
            fs = conn.execute(
                f"""SELECT {', '.join(col_names)} FROM factor_scores
                    WHERE stock_id=? AND calc_date<=? ORDER BY calc_date DESC LIMIT 1""",
                (sid, dt),
            ).fetchone()
            if fs:
                for i, (_, fid) in enumerate(fid_map):
                    if fs[i] is not None:
                        last[fid] = float(fs[i])
            if not last:
                continue
            for fid, val in last.items():
                exists = conn.execute(
                    "SELECT 1 FROM factor_values WHERE stock_id=? AND factor_id=? AND date=?",
                    (sid, fid, dt),
                ).fetchone()
                if not exists:
                    _upsert_factor(conn, sid, dt, fid, val)
                    count += 1
    return count


def _dim(conn: sqlite3.Connection, table: str, col: str, sid: int, dt: str, date_col: str = "date") -> float | None:
    try:
        row = conn.execute(
            f"SELECT {col} FROM {table} WHERE stock_id=? AND {date_col}<=? ORDER BY {date_col} DESC LIMIT 1",
            (sid, dt),
        ).fetchone()
        return float(row[0]) if row and row[0] is not None else None
    except sqlite3.OperationalError:
        return None


def expand_score_history(days: int = 90, db_path: str = None) -> dict:
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

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

    ff_writes = _forward_fill_score_factors(conn, trade_dates)

    inserted = 0
    updated = 0
    for dt in trade_dates:
        for (sid,) in stocks:
            existing = conn.execute(
                "SELECT id FROM comprehensive_scores WHERE stock_id=? AND calc_date=?",
                (sid, dt),
            ).fetchone()

            fs = conn.execute(
                """SELECT composite_score, profitability_score, momentum_score, value_score
                   FROM factor_scores WHERE stock_id=? AND calc_date<=? ORDER BY calc_date DESC LIMIT 1""",
                (sid, dt),
            ).fetchone()

            fv = {}
            for fid, col in [
                ("F001", "composite_score"),
                ("F002", "fundamental_score"),
                ("F003", "technical_score"),
                ("F004", "sentiment_score"),
                ("F005", "capital_score"),
                ("F006", "policy_score"),
                ("F007", "mood_score"),
                ("F008", "val_score"),
            ]:
                row = conn.execute(
                    "SELECT value FROM factor_values WHERE stock_id=? AND factor_id=? AND date=?",
                    (sid, fid, dt),
                ).fetchone()
                if row and row[0] is not None:
                    fv[col] = float(row[0])

            dims = {
                "fundamental_score": (fs["profitability_score"] if fs else None)
                or fv.get("fundamental_score")
                or _dim(conn, "factor_scores", "profitability_score", sid, dt, "calc_date"),
                "technical_score": fv.get("technical_score") or _dim(conn, "tech_analysis_cache", "score", sid, dt, "created_at"),
                "sentiment_score": fv.get("sentiment_score") or _dim(conn, "sentiment_scores", "composite_score", sid, dt),
                "capital_score": fv.get("capital_score") or _dim(conn, "capital_scores", "composite_score", sid, dt),
                "policy_score": fv.get("policy_score") or _dim(conn, "policy_scores", "composite_score", sid, dt),
                "mood_score": fv.get("mood_score") or _dim(conn, "sentiment_scores", "composite_score", sid, dt),
                "val_score": fv.get("val_score") or _dim(conn, "valuation_scores", "composite_score", sid, dt),
            }

            if all(v is None for v in dims.values()) and not (fs and fs["composite_score"] is not None) and not fv.get("composite_score"):
                continue

            # v3.0: composite_score 不再计算和写入；只写维度分，V5 重算在 batch 末尾触发。
            vals = (
                dims["fundamental_score"],
                dims["technical_score"],
                dims["sentiment_score"],
                dims["capital_score"],
                dims["policy_score"],
                dims["mood_score"],
                dims["val_score"],
            )
            if existing:
                conn.execute(
                    """UPDATE comprehensive_scores SET
                       fundamental_score=COALESCE(?, fundamental_score),
                       technical_score=COALESCE(?, technical_score),
                       sentiment_score=COALESCE(?, sentiment_score),
                       capital_score=COALESCE(?, capital_score),
                       policy_score=COALESCE(?, policy_score),
                       mood_score=COALESCE(?, mood_score),
                       val_score=COALESCE(?, val_score)
                       WHERE stock_id=? AND calc_date=?""",
                    (*vals, sid, dt),
                )
                updated += 1
            else:
                conn.execute(
                    """INSERT INTO comprehensive_scores
                       (stock_id, calc_date, fundamental_score, technical_score, sentiment_score,
                        capital_score, policy_score, mood_score, val_score)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (sid, dt, *vals),
                )
                inserted += 1

    score_days = conn.execute("SELECT COUNT(DISTINCT calc_date) FROM comprehensive_scores").fetchone()[0]

    from services.factor_factory import _backfill_score_factors

    factor_writes = _backfill_score_factors(conn)
    conn.commit()
    conn.close()

    return {
        "trade_dates_processed": len(trade_dates),
        "inserted": inserted,
        "updated": updated,
        "score_history_days": score_days,
        "factor_forward_fill_writes": ff_writes,
        "factor_backfill_writes": factor_writes,
        "target_days": 60,
    }
