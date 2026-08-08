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


@router.get("/{code}")
async def etf_detail(code: str):
    """单只 ETF 详情：信息卡(fund_basic) + 行情(本地) + 净值/折溢价(fund_nav) + 前十大持仓(fund_portfolio)。
    各区块独立容错：某个 Tushare 接口挂了不影响其余。"""
    from datetime import date, timedelta

    from database import get as get_db_conn
    from services.tushare_adapter import (
        fetch_etf_info,
        fetch_etf_nav,
        fetch_etf_portfolio_latest,
    )

    conn = get_db_conn()
    e = conn.execute(
        "SELECT ts_code, code, name, management, fund_type, invest_type, list_date "
        "FROM etfs WHERE code=?",
        (code,),
    ).fetchone()
    if not e:
        return {"error": "未找到该 ETF"}
    ts_code = e["ts_code"]

    result: dict = {"code": code, "ts_code": ts_code, "name": e["name"]}

    # 行情（本地已同步，取近 120 交易日）
    q = conn.execute(
        "SELECT trade_date, close, pct_chg, vol, amount FROM etf_daily_quotes "
        "WHERE ts_code=? ORDER BY trade_date DESC LIMIT 120",
        (ts_code,),
    ).fetchall()
    quotes = [dict(r) for r in reversed(q)]
    result["quotes"] = quotes
    last_close = quotes[-1]["close"] if quotes else None

    # 信息卡（实时 fund_basic）
    try:
        result["info"] = fetch_etf_info(ts_code) or {}
    except Exception as ex:  # noqa: BLE001
        result["info"] = {"error": str(ex)[:80]}

    # 净值 + 折溢价（实时 fund_nav，取近 60 天，用最新单位净值对最新收盘价）
    try:
        end = date.today().strftime("%Y%m%d")
        start = (date.today() - timedelta(days=60)).strftime("%Y%m%d")
        nav = fetch_etf_nav(ts_code, start, end)
        navs = sorted((n for n in nav if n.get("unit_nav")), key=lambda x: x["nav_date"])
        result["nav"] = navs
        if navs and last_close:
            un = navs[-1]["unit_nav"]
            result["nav_latest"] = navs[-1]
            result["premium_pct"] = round((last_close / un - 1) * 100, 3) if un else None
    except Exception as ex:  # noqa: BLE001
        result["nav"] = {"error": str(ex)[:80]}

    # 前十大持仓（实时 fund_portfolio，补股票名）
    try:
        pf = fetch_etf_portfolio_latest(ts_code, top=10)
        syms = [(h.get("symbol") or "").split(".")[0] for h in pf.get("holdings", [])]
        syms = [s for s in syms if s]
        namemap: dict = {}
        if syms:
            ph = ",".join("?" for _ in syms)
            for c2, nm in conn.execute(
                f"SELECT code, name FROM stocks WHERE code IN ({ph})", syms
            ):
                namemap[c2] = nm
        for h in pf.get("holdings", []):
            h["name"] = namemap.get((h.get("symbol") or "").split(".")[0])
        result["portfolio"] = pf
    except Exception as ex:  # noqa: BLE001
        result["portfolio"] = {"error": str(ex)[:80]}

    return result
