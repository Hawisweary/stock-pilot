"""辩论批量上下文 — 一次预加载全池数据，避免每股多次 connect。"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import config
from services.score_sql import per_stock_latest_join


@dataclass
class DebateBatchContext:
    today: str
    calc_date: str
    macro_text: str
    stocks: dict[int, dict[str, Any]] = field(default_factory=dict)
    comprehensive: dict[int, dict[str, Any]] = field(default_factory=dict)
    news: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    tech: dict[int, dict[str, Any]] = field(default_factory=dict)
    existing_debate: dict[int, dict[str, Any]] = field(default_factory=dict)


def _active_stocks(conn: sqlite3.Connection, stock_ids: list[int] | None) -> list[tuple[int, str, str, str]]:
    if stock_ids:
        placeholders = ",".join("?" * len(stock_ids))
        rows = conn.execute(
            f"""SELECT id, code, name, industry_sw FROM stocks
                WHERE is_active=1 AND id IN ({placeholders}) ORDER BY id""",
            stock_ids,
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, code, name, industry_sw FROM stocks WHERE is_active=1 ORDER BY id"
        ).fetchall()
    return [(int(r[0]), r[1], r[2] or "", r[3] or "") for r in rows]


def preload_debate_context(stock_ids: list[int] | None = None) -> DebateBatchContext:
    today = date.today().strftime("%Y-%m-%d")
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        calc_date = date.today().strftime("%Y-%m-%d")
        stock_rows = _active_stocks(conn, stock_ids)
        ids = [s[0] for s in stock_rows]

        macro_text = ""
        try:
            macro = conn.execute(
                "SELECT * FROM macro_indicators ORDER BY date DESC LIMIT 1"
            ).fetchone()
            if macro:
                macro_text = (
                    f"PMI:{macro['pmi_manufacturing']} CPI:{macro['cpi_yoy']} "
                    f"LPR:{macro['lpr_1y']}%"
                )
        except sqlite3.OperationalError:
            pass

        ctx = DebateBatchContext(today=today, calc_date=calc_date, macro_text=macro_text)
        for sid, code, name, industry in stock_rows:
            ctx.stocks[sid] = {
                "id": sid,
                "code": code,
                "name": name,
                "industry_sw": industry,
            }

        if not ids:
            return ctx

        placeholders = ",".join("?" * len(ids))
        join_cs = per_stock_latest_join("cs")

        comp_rows = conn.execute(
            f"""SELECT cs.* FROM stocks s
                {join_cs}
                WHERE s.id IN ({placeholders}) AND cs.composite_score IS NOT NULL""",
            ids,
        ).fetchall()
        for row in comp_rows:
            ctx.comprehensive[int(row["stock_id"])] = dict(row)

        news_rows = conn.execute(
            f"""SELECT stock_id, title, sentiment_label, pub_date FROM stock_news
                WHERE stock_id IN ({placeholders})
                ORDER BY stock_id, pub_date DESC""",
            ids,
        ).fetchall()
        for row in news_rows:
            sid = int(row["stock_id"])
            bucket = ctx.news.setdefault(sid, [])
            if len(bucket) < 5:
                bucket.append(dict(row))

        tech_rows = conn.execute(
            f"""SELECT tc.stock_id, tc.signal, tc.score, tc.created_at
                FROM tech_analysis_cache tc
                INNER JOIN (
                    SELECT stock_id, MAX(created_at) AS md
                    FROM tech_analysis_cache
                    WHERE stock_id IN ({placeholders})
                    GROUP BY stock_id
                ) t ON tc.stock_id = t.stock_id AND tc.created_at = t.md""",
            ids,
        ).fetchall()
        for row in tech_rows:
            ctx.tech[int(row["stock_id"])] = {
                "signal": row["signal"],
                "score": row["score"],
            }

        debate_rows = conn.execute(
            f"""SELECT stock_id, date, original_score, adjusted_score, debate_json
                FROM debate_v2
                WHERE date=? AND stock_id IN ({placeholders})""",
            (today, *ids),
        ).fetchall()
        for row in debate_rows:
            ctx.existing_debate[int(row["stock_id"])] = dict(row)

        return ctx
    finally:
        conn.close()


def preload_single_stock_context(stock_id: int) -> DebateBatchContext:
    return preload_debate_context([stock_id])
