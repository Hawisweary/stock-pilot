"""美股列表 + 行情 API。读独立库 us.db(只读连接),不参与 A股/ETF 打分。"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Query

import config

router = APIRouter(prefix="/api/us", tags=["us"])

_SORT = {"dollar_vol": "q.close*q.volume", "pct_chg": "chg", "close": "q.close", "volume": "q.volume"}


def _ro_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{config.US_DB_PATH}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


@router.get("/list")
async def us_list(
    q: str | None = Query(None, description="按 ticker/名称模糊搜索"),
    sort: str = Query("dollar_vol", description="dollar_vol|pct_chg|close|volume"),
    limit: int = Query(200, ge=1, le=3000),
):
    """美股列表(最新交易日行情 + 日涨跌幅)。默认按成交额(close×volume)排序。"""
    try:
        conn = _ro_conn()
    except sqlite3.OperationalError:
        return {"as_of": None, "count": 0, "stocks": [], "error": "美股库未就绪(先跑 us_sync)"}
    try:
        latest_row = conn.execute("SELECT MAX(trade_date) FROM us_daily_quotes").fetchone()
        latest = latest_row[0] if latest_row else None
        sort_col = _SORT.get(sort, "q.close*q.volume")

        where = ["s.is_active=1", "q.trade_date=?"]
        params: list = [latest]
        if q:
            where.append("(s.ticker LIKE ? OR s.name LIKE ?)")
            params += [f"%{q.upper()}%", f"%{q}%"]
        params.append(limit)

        rows = conn.execute(
            f"""SELECT s.ticker, s.name, s.exchange, q.close, q.volume, q.trade_date,
                       (SELECT close FROM us_daily_quotes p
                        WHERE p.ticker=s.ticker AND p.trade_date<q.trade_date
                        ORDER BY p.trade_date DESC LIMIT 1) AS prev_close,
                       CASE WHEN (SELECT close FROM us_daily_quotes p
                                  WHERE p.ticker=s.ticker AND p.trade_date<q.trade_date
                                  ORDER BY p.trade_date DESC LIMIT 1) > 0
                            THEN round((q.close / (SELECT close FROM us_daily_quotes p
                                  WHERE p.ticker=s.ticker AND p.trade_date<q.trade_date
                                  ORDER BY p.trade_date DESC LIMIT 1) - 1) * 100, 2)
                            ELSE NULL END AS chg
                FROM us_stocks s
                JOIN us_daily_quotes q ON q.ticker=s.ticker AND q.trade_date=?
                WHERE {' AND '.join(where)}
                ORDER BY {sort_col} IS NULL, {sort_col} DESC
                LIMIT ?""",
            [latest] + params,
        ).fetchall()
        return {"as_of": latest, "count": len(rows), "stocks": [dict(r) for r in rows]}
    finally:
        conn.close()
