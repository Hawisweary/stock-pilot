from config import DB_PATH

"""a-stock-data + TradingAgents 集成 API"""
from fastapi import APIRouter, HTTPException, Query
from api_utils import execute_sql

router = APIRouter(prefix="/api", tags=["signals"])


# ── 同花顺热点 ──
@router.post("/signals/hotspots/sync")
async def sync_hotspots():
    from services.astock_data import sync_ths_hotspots
    from services.market_data_cache import _ths_hotspots_cache

    out = sync_ths_hotspots()
    _ths_hotspots_cache.invalidate()
    return out


@router.get("/signals/hotspots")
async def get_hotspots(force: bool = Query(False, description="跳过热点读缓存")):
    import sqlite3
    from services.market_data_cache import cached_ths_hotspots

    def _load() -> dict:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        max_date = conn.execute("SELECT MAX(date) FROM ths_hotspots").fetchone()[0]
        rows = []
        if max_date:
            rows = conn.execute(
                "SELECT * FROM ths_hotspots WHERE date=? ORDER BY change_pct DESC LIMIT 10",
                (max_date,),
            ).fetchall()
        conn.close()
        return {
            "hotspots": [dict(r) for r in rows],
            "date": max_date,
            "cached": False,
        }

    cache_key = "ths-hotspots"
    out = cached_ths_hotspots(cache_key, force, _load)
    if not force:
        out = dict(out)
        out["cached"] = True
    return out


# ── 龙虎榜 ──
@router.get("/signals/dragon-tiger")
async def dragon_tiger_daily(
    date: str | None = None,
    limit: int = 80,
    force: bool = Query(False, description="跳过龙虎榜缓存并重新拉取"),
):
    """全市场当日龙虎榜：优先 lhb_market_daily，缺失再拉东财"""
    from services.lhb_fetch import fetch_lhb_daily

    return fetch_lhb_daily(date, limit=limit, force=force)


@router.get("/signals/dragon-tiger/{code}")
async def dragon_tiger(code: str, date: str | None = None, days: int = 60, limit: int = 10):
    """单股龙虎榜；date=YYYY-MM-DD 时返回该日席位明细"""
    from services.lhb_fetch import fetch_lhb_stock

    return fetch_lhb_stock(code, date, history_days=days, limit=limit)


# ── 限售解禁 ──
@router.get("/signals/unlock/{code}")
async def unlock_alert(code: str, days: int = 90):
    from services.astock_data import unlock_calendar
    return {"code": code, "unlocks": unlock_calendar(code, days)}


# ── 融资融券 ──
@router.get("/signals/margin/{code}")
async def margin_detail(code: str):
    from services.astock_data import margin_trading
    return {"code": code, "history": margin_trading(code)}


# ── 大宗交易 ──
@router.get("/signals/block-trade/{code}")
async def block_trades(code: str):
    from services.astock_data import block_trade
    return {"code": code, "trades": block_trade(code)}


# ── 股东户数 ──
@router.get("/signals/shareholders/{code}")
async def shareholders(code: str):
    from services.astock_data import shareholder_change
    return {"code": code, "history": shareholder_change(code)}


# ── 分红历史 ──
@router.get("/signals/dividends/{code}")
async def dividends(code: str):
    from services.astock_data import dividend_history
    return {"code": code, "history": dividend_history(code)}


# ── mootdx 财务同步 ──
@router.post("/financials/mootdx-sync")
async def sync_mootdx_all():
    import threading
    def _run():
        stocks = execute_sql("SELECT code FROM stocks WHERE is_active=1")
        from services.astock_data import sync_mootdx_financials
        for i, s in enumerate(stocks):
            try:
                r = sync_mootdx_financials(s["code"])
                print(f"[mootdx财务] {i+1}/{len(stocks)} {s['code']} records={r.get('records',0)}")
            except Exception as e:
                print(f"[mootdx财务] {s['code']} 失败: {e}")
    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


@router.post("/financials/mootdx/{code}")
async def sync_mootdx_single(code: str):
    from services.astock_data import sync_mootdx_financials
    return sync_mootdx_financials(code)


# ── 新浪财报 ──
@router.get("/financials/sina/{code}")
async def sina_report(code: str, report_type: str = "lrb"):
    """report_type: lrb(利润表) | fzb(负债表) | llb(现金流)"""
    from services.astock_data import sina_financial_report
    return {"code": code, "type": report_type, "data": sina_financial_report(code, report_type)}


# ── 概念板块归属 ──
@router.get("/concept-boards/{code}")
async def concept_boards(code: str):
    """个股所属全部板块（行业/概念/地域）+ BK码 + 涨跌幅。本地缓存优先，无则东财直连。"""
    from services.astock_data import concept_boards as _boards
    boards = _boards(code)
    return {"code": code, "count": len(boards), "boards": boards}


@router.post("/concept-boards/sync-all")
async def sync_concept_boards():
    """批量同步全部持仓股票的东财板块数据（约107只 × 1s = ~2分钟）。"""
    import asyncio
    from services.astock_data import sync_concept_boards_all
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, sync_concept_boards_all)
    return result


# ── 同花顺一致预期 EPS ──
@router.get("/consensus-eps/{code}")
async def consensus_eps(code: str):
    """同花顺分析师一致预期 EPS（近3年）。"""
    from services.astock_data import consensus_eps as _eps
    return {"code": code, **_eps(code)}
