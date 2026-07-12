"""V5 情绪面代理评分 — 换手极端 + 新闻过热 + 主力流出；狂热翻转规则。

北向日频净买自 2024-08 停更，V5 情绪面不依赖北向（见 northbound_fetch.py）。
"""
from __future__ import annotations

import sqlite3
from datetime import date

import config
from config import latest_trading_date


def apply_v5_flip(mood_tier: int, capital_tier: int) -> tuple[int, bool]:
    """V5 翻转：狂热+资金≤0 压至≤0；恐慌+资金≥+1 升至+1。"""
    flipped = False
    if mood_tier >= 2 and capital_tier <= 0:
        mood_tier = min(mood_tier, 0)
        flipped = True
    elif mood_tier <= -2 and capital_tier >= 1:
        mood_tier = 1
        flipped = True
    return mood_tier, flipped


def _tier_from_raw(raw: float) -> int:
    if raw >= 80:
        return 2
    if raw >= 65:
        return 1
    if raw <= 20:
        return -2
    if raw <= 35:
        return -1
    return 0


def _capital_tier_from_flow(main_net_5d: float | None) -> int:
    if main_net_5d is None:
        return 0
    if main_net_5d > 0:
        return 1
    if main_net_5d < 0:
        return -1
    return 0


def _percentile_rank(value: float, population: list[float]) -> float:
    if not population:
        return 50.0
    n = len(population)
    rank = sum(1 for v in population if v <= value) / n
    return rank * 100


def compute_mood_proxy(
    stock_id: int,
    *,
    calc_date: str | None = None,
    market_limit_up: int | None = None,
) -> dict:
    """单股 V5 情绪代理分与档位（含翻转）。"""
    as_of = calc_date or latest_trading_date() or date.today().isoformat()
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        hist = conn.execute(
            """SELECT turnover FROM stock_daily_quotes
               WHERE stock_id=? AND turnover IS NOT NULL
               ORDER BY trade_date DESC LIMIT 60""",
            (stock_id,),
        ).fetchall()
        turnovers = [float(r[0]) for r in hist if r[0] is not None]
        turnover_now = turnovers[0] if turnovers else 0.0
        turnover_pct = _percentile_rank(turnover_now, turnovers) if turnovers else 50.0

        news_row = conn.execute(
            """SELECT AVG(sentiment_score) AS avg_s, COUNT(*) AS cnt
               FROM stock_news
               WHERE stock_id=? AND sentiment_score IS NOT NULL
                 AND pub_date >= date(?, '-14 days')""",
            (stock_id, as_of),
        ).fetchone()
        news_avg = float(news_row["avg_s"]) if news_row and news_row["avg_s"] else 50.0
        news_cnt = int(news_row["cnt"] or 0) if news_row else 0
        news_heat = min(100.0, max(0.0, (news_avg - 50) * 1.2 + news_cnt * 3))

        flow_row = conn.execute(
            """SELECT main_net_5d, main_net_inflow FROM stock_fund_flow_daily
               WHERE stock_id=? ORDER BY trade_date DESC LIMIT 1""",
            (stock_id,),
        ).fetchone()
        main_net_5d = float(flow_row["main_net_5d"]) if flow_row and flow_row["main_net_5d"] is not None else None
        flow_heat = 0.0
        if main_net_5d is not None and main_net_5d < 0:
            flow_heat = min(30.0, abs(main_net_5d) / 1e8 * 5)

        market_heat = 0.0
        if market_limit_up is not None and market_limit_up >= 80:
            market_heat = 15.0
        elif market_limit_up is not None and market_limit_up >= 50:
            market_heat = 8.0

        mood_raw = (
            turnover_pct * 0.45
            + news_heat * 0.30
            + flow_heat * 0.15
            + market_heat * 0.10
        )
        mood_raw = max(0.0, min(100.0, mood_raw))
        mood_tier = _tier_from_raw(mood_raw)
        capital_tier = _capital_tier_from_flow(main_net_5d)

        mood_tier, flipped = apply_v5_flip(mood_tier, capital_tier)

        return {
            "stock_id": stock_id,
            "calc_date": as_of,
            "mood_raw": round(mood_raw, 2),
            "mood_tier": mood_tier,
            "turnover_pct": round(turnover_pct, 2),
            "news_heat": round(news_heat, 2),
            "main_net_5d": main_net_5d,
            "capital_tier": capital_tier,
            "flipped": flipped,
        }
    finally:
        conn.close()


def _market_limit_up_count() -> int | None:
    try:
        from services.data_sources import tencent_quote

        conn = sqlite3.connect(config.DB_PATH)
        stocks = conn.execute(
            "SELECT code FROM stocks WHERE is_active=1"
        ).fetchall()
        conn.close()
        quotes = tencent_quote([s[0] for s in stocks])
        limit_up = 0
        for code, _ in stocks:
            q = quotes.get(code, {})
            chg = q.get("change_pct", 0) or 0
            if chg >= 9.8:
                limit_up += 1
        return limit_up
    except Exception:
        return None


def compute_all_mood_v5(
    stock_ids: list[int] | None = None,
    *,
    calc_date: str | None = None,
) -> dict:
    """批量计算并持久化 V5 情绪代理。"""
    as_of = calc_date or latest_trading_date() or date.today().isoformat()
    limit_up = _market_limit_up_count()

    conn = sqlite3.connect(config.DB_PATH)
    try:
        if stock_ids:
            ph = ",".join("?" * len(stock_ids))
            ids = [
                int(r[0])
                for r in conn.execute(
                    f"SELECT id FROM stocks WHERE id IN ({ph}) AND is_active=1",
                    stock_ids,
                ).fetchall()
            ]
        else:
            ids = [
                int(r[0])
                for r in conn.execute(
                    "SELECT id FROM stocks WHERE is_active=1"
                ).fetchall()
            ]
    finally:
        conn.close()

    computed = 0
    flipped_n = 0
    conn = sqlite3.connect(config.DB_PATH)
    try:
        for sid in ids:
            m = compute_mood_proxy(sid, calc_date=as_of, market_limit_up=limit_up)
            conn.execute(
                """INSERT OR REPLACE INTO stock_mood_v5_daily
                (stock_id, calc_date, mood_raw, mood_tier, turnover_pct,
                 news_heat, main_net_5d, capital_tier, flipped, source)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    sid,
                    as_of,
                    m["mood_raw"],
                    m["mood_tier"],
                    m["turnover_pct"],
                    m["news_heat"],
                    m.get("main_net_5d"),
                    m["capital_tier"],
                    1 if m["flipped"] else 0,
                    "proxy_v5",
                ),
            )
            computed += 1
            if m["flipped"]:
                flipped_n += 1
        conn.commit()
    finally:
        conn.close()

    return {
        "calc_date": as_of,
        "computed": computed,
        "flipped": flipped_n,
        "market_limit_up": limit_up,
    }


def get_stock_mood_v5(stock_id: int) -> dict | None:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """SELECT stock_id, calc_date, mood_raw, mood_tier, turnover_pct,
                      news_heat, main_net_5d, capital_tier, flipped, source
               FROM stock_mood_v5_daily WHERE stock_id=?
               ORDER BY calc_date DESC LIMIT 1""",
            (stock_id,),
        ).fetchone()
        if row:
            d = dict(row)
            d["flipped"] = bool(d.get("flipped"))
            return d
        return compute_mood_proxy(stock_id)
    finally:
        conn.close()
