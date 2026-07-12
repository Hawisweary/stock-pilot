"""个股新闻 sentiment_score 聚合 — 时间衰减加权平均"""
from __future__ import annotations

import sqlite3

DEFAULT_WINDOW_DAYS = 7

_BATCH_SENTIMENT_SQL = """
SELECT stock_id,
       SUM(sentiment_score * POWER(0.85, julianday(date(?)) - julianday(date(pub_date))))
       / NULLIF(SUM(POWER(0.85, julianday(date(?)) - julianday(date(pub_date)))), 0)
       AS sentiment_avg
FROM stock_news
WHERE stock_id IN ({ph})
  AND sentiment_score IS NOT NULL
  AND date(pub_date) >= date(?, '-' || ? || ' days')
  AND date(pub_date) <= date(?)
GROUP BY stock_id
"""


def batch_get_sentiment_scores(
    conn: sqlite3.Connection,
    stock_ids: list[int],
    target_date: str,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> dict[int, float]:
    """批量获取 [target_date - window_days, target_date] 闭区间内的新闻 sentiment 加权均值。"""
    if not stock_ids:
        return {}
    placeholders = ",".join(["?"] * len(stock_ids))
    params = (
        target_date,
        target_date,
        *stock_ids,
        target_date,
        str(window_days),
        target_date,
    )
    rows = conn.execute(
        _BATCH_SENTIMENT_SQL.format(ph=placeholders),
        params,
    ).fetchall()
    return {int(r[0]): round(float(r[1]), 2) for r in rows if r[1] is not None}


def resolve_sentiment_scores(
    conn: sqlite3.Connection,
    stock_ids: list[int],
    target_date: str,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> dict[int, float]:
    """7 日聚合 → 30 日加权 → peer 代理 → 默认分。"""
    scores = batch_get_sentiment_scores(conn, stock_ids, target_date, window_days=window_days)
    missing = [sid for sid in stock_ids if sid not in scores]
    if not missing:
        return scores

    from services.news_fetcher import compute_weighted_news_score

    for sid in missing:
        ws = compute_weighted_news_score(sid)
        if ws.get("count", 0) > 0:
            scores[sid] = float(ws["score"])
            continue

        try:
            row = conn.execute(
                "SELECT industry_sw FROM stocks WHERE id=? LIMIT 1", (sid,)
            ).fetchone()
            ind = row[0] if row else ""
        except sqlite3.OperationalError:
            ind = ""
        try:
            peer = conn.execute(
                """SELECT n.stock_id FROM stock_news n
                   JOIN stocks s ON n.stock_id=s.id
                   WHERE s.industry_sw=? AND s.is_active=1 AND n.stock_id!=?
                     AND n.sentiment_score IS NOT NULL
                   GROUP BY n.stock_id ORDER BY COUNT(*) DESC LIMIT 1""",
                (ind, sid),
            ).fetchone()
        except sqlite3.OperationalError:
            peer = None
        if peer:
            ps = compute_weighted_news_score(int(peer[0]))
            if ps.get("count", 0) > 0:
                scores[sid] = round(ps["score"] * 0.7 + 50 * 0.3, 2)
                continue
        # C2 fix: 无新闻且无同行情感数据时不填默认分，让 V5 跳过此维度。
    return scores


def get_stock_sentiment_score(
    stock_id: int,
    target_date: str,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    conn: sqlite3.Connection | None = None,
) -> float | None:
    """单股 sentiment 聚合；无有效新闻时返回 None。"""
    own_conn = conn is None
    if own_conn:
        import config

        conn = sqlite3.connect(config.DB_PATH)
    try:
        result = batch_get_sentiment_scores(conn, [stock_id], target_date, window_days=window_days)
        return result.get(stock_id)
    finally:
        if own_conn:
            conn.close()
