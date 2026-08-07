"""ETF（场内基金）列表 + 行情 API。独立于个股，不参与 V5 评分。"""
from __future__ import annotations

from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/etf", tags=["etf"])

_SORT_COLS = {"amount": "q.amount", "pct_chg": "q.pct_chg", "close": "q.close"}


@router.get("/list")
async def etf_list(
    q: str | None = Query(None, description="按代码/名称模糊搜索"),
    sort: str = Query("amount", description="排序字段: amount|pct_chg|close"),
    limit: int = Query(200, ge=1, le=2000),
):
    """ETF 列表（含最新交易日行情）。"""
    from database import get as get_db_conn

    conn = get_db_conn()
    latest_row = conn.execute("SELECT MAX(trade_date) FROM etf_daily_quotes").fetchone()
    latest = latest_row[0] if latest_row else None
    sort_col = _SORT_COLS.get(sort, "q.amount")

    where = ["e.is_active=1"]
    params: list = [latest]
    if q:
        where.append("(e.code LIKE ? OR e.name LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    params.append(limit)

    rows = conn.execute(
        f"""SELECT e.code, e.name, e.management, e.fund_type, e.invest_type, e.list_date,
                   q.close, q.pct_chg, q.change, q.vol, q.amount, q.trade_date
            FROM etfs e
            LEFT JOIN etf_daily_quotes q ON q.ts_code=e.ts_code AND q.trade_date=?
            WHERE {' AND '.join(where)}
            ORDER BY {sort_col} DESC
            LIMIT ?""",
        params,
    ).fetchall()

    return {"as_of": latest, "count": len(rows), "etfs": [dict(r) for r in rows]}
