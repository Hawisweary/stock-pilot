"""美股 ETF 列表 + 详情 API。读 us.db(us_etfs / us_etf_profile),行情复用 us_daily_quotes。

K线复用 /api/us/{ticker}/klines(同表),本模块只提供列表与 ETF 富字段详情。
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3

from fastapi import APIRouter, Query

import config

router = APIRouter(prefix="/api/us-etf", tags=["us-etf"])

_SORT = {"aum": "e.aum", "dollar_vol": "q.close*q.volume", "pct_chg": "chg",
         "close": "q.close", "expense": "e.expense_ratio"}


def _ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{config.US_DB_PATH}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


@router.get("/list")
async def etf_list(
    q: str | None = Query(None),
    sort: str = Query("aum", description="aum|dollar_vol|pct_chg|close|expense"),
    limit: int = Query(300, ge=1, le=1000),
):
    """ETF 列表(元信息 + 最新行情 + 日涨跌)。默认按规模(AUM)排序。"""
    try:
        conn = _ro()
    except sqlite3.OperationalError:
        return {"as_of": None, "count": 0, "etfs": [], "error": "ETF 库未就绪(先跑 us_etf_sync)"}
    try:
        latest = conn.execute("SELECT MAX(trade_date) FROM us_daily_quotes").fetchone()[0]
        sort_col = _SORT.get(sort, "e.aum")
        where = ["e.is_active=1"]
        params: list = [latest]
        if q:
            where.append("(e.ticker LIKE ? OR e.name LIKE ? OR e.category LIKE ?)")
            params += [f"%{q.upper()}%", f"%{q}%", f"%{q}%"]
        params.append(limit)
        rows = conn.execute(
            f"""SELECT e.ticker,e.name,e.category,e.fund_family,e.aum,e.expense_ratio,
                       q.close,q.volume,q.trade_date,
                       (SELECT close FROM us_daily_quotes p WHERE p.ticker=e.ticker
                        AND p.trade_date<q.trade_date ORDER BY p.trade_date DESC LIMIT 1) AS prev,
                       CASE WHEN (SELECT close FROM us_daily_quotes p WHERE p.ticker=e.ticker
                            AND p.trade_date<q.trade_date ORDER BY p.trade_date DESC LIMIT 1)>0
                        THEN round((q.close/(SELECT close FROM us_daily_quotes p WHERE p.ticker=e.ticker
                            AND p.trade_date<q.trade_date ORDER BY p.trade_date DESC LIMIT 1)-1)*100,2)
                        ELSE NULL END AS chg
                FROM us_etfs e
                LEFT JOIN us_daily_quotes q ON q.ticker=e.ticker AND q.trade_date=?
                WHERE {' AND '.join(where)}
                ORDER BY {sort_col} IS NULL, {sort_col} DESC
                LIMIT ?""",
            params,
        ).fetchall()
        return {"as_of": latest, "count": len(rows), "etfs": [dict(r) for r in rows]}
    finally:
        conn.close()


def _get_etf_profile(ticker: str, ttl_hours: float = 20.0) -> dict:
    """ETF profile 缓存优先;冷门/过期则现抓并回写。"""
    try:
        conn = sqlite3.connect(config.US_DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError:
        from services.us_market_adapter import fetch_us_etf_profile
        return fetch_us_etf_profile(ticker) or {}
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS us_etf_profile (
            ticker TEXT PRIMARY KEY, data TEXT, updated_at TEXT)""")
        row = conn.execute("SELECT data,updated_at FROM us_etf_profile WHERE ticker=?", (ticker,)).fetchone()
        if row:
            try:
                age = (dt.datetime.now() - dt.datetime.fromisoformat(row["updated_at"])).total_seconds()
                if age < ttl_hours * 3600:
                    return json.loads(row["data"])
            except (ValueError, TypeError):
                pass
        from services.us_market_adapter import fetch_us_etf_profile
        p = fetch_us_etf_profile(ticker)
        if not p:
            return json.loads(row["data"]) if row else {}
        conn.execute("INSERT OR REPLACE INTO us_etf_profile(ticker,data,updated_at) VALUES(?,?,?)",
                     (ticker, json.dumps(p, ensure_ascii=False), dt.datetime.now().isoformat()))
        conn.commit()
        return p
    finally:
        conn.close()


@router.get("/{ticker}")
def etf_detail(ticker: str):
    """单只 ETF 详情:元信息 + profile(持仓/板块/配置/费率/回报)+ 近120日行情。K线另走 /api/us/{ticker}/klines。"""
    ticker = ticker.upper()
    try:
        conn = _ro()
    except sqlite3.OperationalError:
        return {"error": "ETF 库未就绪"}
    try:
        meta = conn.execute("SELECT ticker,name,category,fund_family,aum,expense_ratio FROM us_etfs WHERE ticker=?",
                            (ticker,)).fetchone()
        qq = conn.execute("SELECT trade_date,close,volume FROM us_daily_quotes WHERE ticker=? "
                          "ORDER BY trade_date DESC LIMIT 120", (ticker,)).fetchall()
        quotes = [dict(r) for r in reversed(qq)]
    finally:
        conn.close()
    if not meta:
        return {"error": "未找到该 ETF"}
    prof = _get_etf_profile(ticker)
    return {"ticker": ticker, "name": meta["name"] or prof.get("name"),
            "category": meta["category"], "fund_family": meta["fund_family"],
            "quotes": quotes, "profile": prof}
