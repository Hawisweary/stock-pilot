"""综合评分计算 — 通过 comprehensive_store 写入，不覆盖已有维度分"""
from __future__ import annotations

import sqlite3

import config
from database import write_lock
from services.comprehensive_store import load_latest_comprehensive_rows, resolve_calc_date
from services.comprehensive_store import recompute_composite, upsert_dimension_score  # noqa: recompute_composite kept for callers
from services.score_gap_scanner import ALL_SYNC_DIMENSIONS, is_source_stale, _batch_source_date_maps
from services.sentiment_aggregate import batch_get_sentiment_scores, resolve_sentiment_scores

TABLE_LATEST_SQL = {
    "fundamental_score": """
        SELECT fs.stock_id, fs.composite_score
        FROM factor_scores fs
        INNER JOIN (
            SELECT stock_id, MAX(calc_date) AS md FROM factor_scores
            WHERE stock_id IN ({ph}) GROUP BY stock_id
        ) t ON fs.stock_id = t.stock_id AND fs.calc_date = t.md
    """,
    "technical_score": """
        SELECT tc.stock_id, tc.score
        FROM tech_analysis_cache tc
        INNER JOIN (
            SELECT stock_id, MAX(created_at) AS md FROM tech_analysis_cache
            WHERE stock_id IN ({ph}) GROUP BY stock_id
        ) t ON tc.stock_id = t.stock_id AND tc.created_at = t.md
    """,
    "capital_score": """
        SELECT cs.stock_id, cs.composite_score
        FROM capital_scores cs
        INNER JOIN (
            SELECT stock_id, MAX(date) AS md FROM capital_scores
            WHERE stock_id IN ({ph}) GROUP BY stock_id
        ) t ON cs.stock_id = t.stock_id AND cs.date = t.md
    """,
    "policy_score": """
        SELECT ps.stock_id, ps.composite_score
        FROM policy_scores ps
        INNER JOIN (
            SELECT stock_id, MAX(date) AS md FROM policy_scores
            WHERE stock_id IN ({ph}) GROUP BY stock_id
        ) t ON ps.stock_id = t.stock_id AND ps.date = t.md
    """,
    "mood_score": """
        SELECT ss.stock_id, ss.composite_score
        FROM sentiment_scores ss
        INNER JOIN (
            SELECT stock_id, MAX(date) AS md FROM sentiment_scores
            WHERE stock_id IN ({ph}) GROUP BY stock_id
        ) t ON ss.stock_id = t.stock_id AND ss.date = t.md
    """,
    "val_score": """
        SELECT vs.stock_id, vs.composite_score
        FROM valuation_scores vs
        INNER JOIN (
            SELECT stock_id, MAX(date) AS md FROM valuation_scores
            WHERE stock_id IN ({ph}) GROUP BY stock_id
        ) t ON vs.stock_id = t.stock_id AND vs.date = t.md
    """,
}


def _latest_map(conn: sqlite3.Connection, sql: str, stock_ids: list[int]) -> dict[int, float]:
    if not stock_ids:
        return {}
    placeholders = ",".join(["?"] * len(stock_ids))
    rows = conn.execute(sql.format(ph=placeholders), tuple(stock_ids)).fetchall()
    return {int(r[0]): float(r[1]) for r in rows if r[1] is not None}


def _load_active_stock_ids(conn: sqlite3.Connection, stock_ids: list[int] | None) -> list[int]:
    if stock_ids:
        return stock_ids
    rows = conn.execute("SELECT id FROM stocks WHERE is_active=1 ORDER BY id").fetchall()
    return [int(r[0]) for r in rows]


def _load_comprehensive_rows(
    conn: sqlite3.Connection, stock_ids: list[int], calc_date: str
) -> dict[int, sqlite3.Row]:
    if not stock_ids:
        return {}
    placeholders = ",".join(["?"] * len(stock_ids))
    cols = ", ".join(ALL_SYNC_DIMENSIONS)
    rows = conn.execute(
        f"""
        SELECT stock_id, {cols}
        FROM comprehensive_scores
        WHERE calc_date=? AND stock_id IN ({placeholders})
        """,
        (calc_date, *stock_ids),
    ).fetchall()
    return {int(r["stock_id"]): r for r in rows}


def sync_all_dimensions(
    stock_ids: list[int] | None = None,
    calc_date: str | None = None,
    *,
    overwrite: bool = False,
    refresh_stale: bool = False,
) -> dict:
    """
    七维从源表批量同步到 comprehensive_scores。
    write_lock + 单事务；v3.0 不再调 recompute_composite，
    返回 changed_stock_ids 供调用方触发 Path B V5 重算。
    """
    synced = {col: 0 for col in ALL_SYNC_DIMENSIONS}
    skipped = {col: 0 for col in ALL_SYNC_DIMENSIONS}
    unchanged = {col: 0 for col in ALL_SYNC_DIMENSIONS}
    refreshed_stale = {col: 0 for col in ALL_SYNC_DIMENSIONS}

    with write_lock:
        conn = sqlite3.connect(config.DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            ids = _load_active_stock_ids(conn, stock_ids)
            date = calc_date or config.latest_trading_date()
            if not ids:
                return {
                    "calc_date": date,
                    "synced": synced,
                    "skipped": skipped,
                    "unchanged": unchanged,
                    "status": "done",
                }

            source_maps: dict[str, dict[int, float]] = {}
            for dim, sql in TABLE_LATEST_SQL.items():
                source_maps[dim] = _latest_map(conn, sql, ids)
            source_maps["sentiment_score"] = (
                resolve_sentiment_scores(conn, ids, date)
                if refresh_stale
                else batch_get_sentiment_scores(conn, ids, date)
            )
            source_dates = _batch_source_date_maps(conn, ids) if refresh_stale else {}

            existing_latest = load_latest_comprehensive_rows(conn, ids)
            affected: set[tuple[int, str]] = set()

            for sid in ids:
                row = existing_latest.get(sid)
                sync_date = (
                    str(row["calc_date"])
                    if row and row.get("calc_date")
                    else resolve_calc_date(conn, sid)
                )
                updates: dict[str, float] = {}

                for col in ALL_SYNC_DIMENSIONS:
                    value = source_maps.get(col, {}).get(sid)
                    if value is None:
                        skipped[col] += 1
                        continue

                    current = row[col] if row else None
                    if current is not None and not overwrite:
                        src_date = source_dates.get(col, {}).get(sid) if refresh_stale else None
                        if refresh_stale and is_source_stale(src_date, sync_date):
                            updates[col] = value
                            refreshed_stale[col] += 1
                            synced[col] += 1
                        else:
                            unchanged[col] += 1
                        continue

                    if current is None or overwrite:
                        updates[col] = value
                        synced[col] += 1

                if not updates:
                    continue

                has_row = conn.execute(
                    "SELECT 1 FROM comprehensive_scores WHERE stock_id=? AND calc_date=?",
                    (sid, sync_date),
                ).fetchone()
                if has_row:
                    set_clause = ", ".join(f"{col}=?" for col in updates)
                    conn.execute(
                        f"UPDATE comprehensive_scores SET {set_clause} WHERE stock_id=? AND calc_date=?",
                        (*updates.values(), sid, sync_date),
                    )
                else:
                    cols = ["stock_id", "calc_date", *updates.keys()]
                    placeholders = ", ".join(["?"] * len(cols))
                    conn.execute(
                        f"INSERT INTO comprehensive_scores ({', '.join(cols)}) VALUES ({placeholders})",
                        (sid, sync_date, *updates.values()),
                    )

                affected.add((sid, sync_date))

            conn.commit()
            changed_stock_ids = sorted({sid for sid, _ in affected})
            return {
                "calc_date": date,
                "synced": synced,
                "skipped": skipped,
                "unchanged": unchanged,
                "refreshed_stale": refreshed_stale,
                "affected_stocks": len(changed_stock_ids),
                "changed_stock_ids": changed_stock_ids,
                "status": "done",
            }
        finally:
            conn.close()


def calculate_all(stock_ids: list[int]) -> dict:
    """将 factor/tech/news 同步到 comprehensive_scores 并重算综合分（批量 SQL）。"""
    if not stock_ids:
        return {"updated": 0, "calc_date": config.latest_trading_date(), "status": "done"}

    calc_date = config.latest_trading_date()
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row

    fund_map = _latest_map(conn, TABLE_LATEST_SQL["fundamental_score"], stock_ids)
    tech_map = _latest_map(conn, TABLE_LATEST_SQL["technical_score"], stock_ids)
    news_map = _latest_map(
        conn,
        """
        SELECT n.stock_id, n.sentiment_score
        FROM stock_news n
        INNER JOIN (
            SELECT stock_id, MAX(pub_date) AS md FROM stock_news
            WHERE stock_id IN ({ph}) AND sentiment_score IS NOT NULL
            GROUP BY stock_id
        ) t ON n.stock_id = t.stock_id AND n.pub_date = t.md
        WHERE n.sentiment_score IS NOT NULL
        """,
        stock_ids,
    )
    conn.close()

    for sid in stock_ids:
        if sid in fund_map:
            upsert_dimension_score(sid, "fundamental_score", fund_map[sid], calc_date=calc_date)
        if sid in tech_map:
            upsert_dimension_score(sid, "technical_score", tech_map[sid], calc_date=calc_date)
        if sid in news_map:
            upsert_dimension_score(sid, "sentiment_score", news_map[sid], calc_date=calc_date)

    return {"updated": len(stock_ids), "calc_date": calc_date, "status": "done"}
