from config import DB_PATH

"""东财数据 + AI辩论 + 数据融合 + 宏观指标 API"""
import asyncio
from datetime import date

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from api_utils import execute_sql

router = APIRouter(prefix="/api", tags=["advanced"])


_DEBATE_GONE = HTTPException(
    status_code=410,
    detail={
        "error": "debate_removed",
        "message": "辩论链路已在 v3.0 移除。综合分权威来源：composite_v5。",
        "migration": "use GET /api/stocks/{market} field score (= composite_v5)",
    },
)


class DebateBatchBody(BaseModel):
    """v3.0: DebateBatchBody 仅保留供 410 路由接收请求体，不再执行。"""
    mode: str = "tiered"
    concurrency: int | None = None
    skip_unchanged: bool | None = None
    write_composite: bool | None = None
    stock_ids: list[int] | None = None
    priority_top_n: int | None = None
    priority_bottom_n: int | None = None
    retry_job_id: str | None = None
    dry_run: bool = False


@router.post("/eastmoney/sync")
async def sync_eastmoney():
    """同步东财特色数据"""
    from services.eastmoney_sync import sync_eastmoney_data
    return sync_eastmoney_data()


@router.post("/macro/backfill")
async def macro_backfill(days: int = Query(252, ge=30, le=500)):
    """回填近 N 日 10Y 国债 / USD-CNH。"""
    from services.macro_sync import backfill_macro_rates

    return backfill_macro_rates(days=days)


@router.post("/lhb/sync")
async def lhb_sync(
    mode: str = Query("watchlist", description="watchlist | market | backfill"),
    days: int = Query(60, ge=7, le=730),
    years: int = Query(2, ge=1, le=3),
):
    """龙虎榜历史入库。"""
    from services.lhb_sync import (
        backfill_lhb_history,
        sync_lhb_market_days,
        sync_lhb_watchlist,
    )

    if mode == "market":
        return sync_lhb_market_days(days=days)
    if mode == "backfill":
        return backfill_lhb_history(years=years)
    return sync_lhb_watchlist()


@router.get("/eastmoney/margin/{stock_id}")
async def get_margin(stock_id: int):
    """获取个股融资融券"""
    import sqlite3
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    stock = conn.execute("SELECT code FROM stocks WHERE id=?", (stock_id,)).fetchone()
    if not stock: raise HTTPException(status_code=404)
    row = conn.execute(
        "SELECT * FROM eastmoney_margin WHERE stock_id=? ORDER BY date DESC LIMIT 1",
        (stock_id,),
    ).fetchone()
    conn.close()
    return {"stock_id": stock_id, "margin": dict(row) if row else None}


# v3.0: 辩论链路已移除。以下路由统一返回 410 Gone，保留注册避免客户端硬错误。

@router.get("/debate/batch/plan")
async def debate_batch_plan(**_):
    raise _DEBATE_GONE


@router.post("/debate/batch")
async def batch_debate(body: DebateBatchBody | None = None):
    raise _DEBATE_GONE


@router.get("/debate/batch/history")
async def debate_batch_history(**_):
    raise _DEBATE_GONE


@router.post("/debate/{stock_id}")
async def run_debate(stock_id: int):
    raise _DEBATE_GONE


@router.get("/debate/{stock_id}")
async def get_debate(stock_id: int):
    raise _DEBATE_GONE


# ── 数据融合 ──

@router.post("/fusion/sync")
async def run_fusion():
    """全量数据融合验证"""
    import threading
    def _run():
        stocks = execute_sql("SELECT id, code FROM stocks WHERE is_active=1")
        from services.data_fusion import fusion_quote
        for i, s in enumerate(stocks):
            try:
                r = fusion_quote(s["id"], s["code"])
                print(f"[融合] {i+1}/{len(stocks)} {s['code']} {r['validation']['status']}")
            except Exception as e:
                print(f"[融合] {s['code']} 失败: {e}")
    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


@router.get("/fusion/quality")
async def data_quality():
    """数据质量概览"""
    import sqlite3
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    today = date.today().strftime("%Y-%m-%d")
    try:
        rows = conn.execute(
            "SELECT * FROM data_quality WHERE date=? ORDER BY deviation_pct DESC", (today,)
        ).fetchall()
    except Exception:
        import logging; logging.getLogger(__name__).exception("data_quality query failed date=%s", today)
        conn.close()
        return {"summary": {"total": 0, "green": 0, "yellow": 0, "red": 0, "no_data": 34}, "details": []}
    conn.close()
    summary = {"total": len(rows), "green": 0, "yellow": 0, "red": 0, "no_data": 0}
    for r in rows:
        st = r["status"] or "no_data"
        if st not in summary: summary[st] = 0
        summary[st] += 1
    return {"summary": summary, "details": [dict(r) for r in rows[:10]]}


# ── 宏观指标 ──

@router.post("/macro/sync")
async def sync_macro():
    """同步宏观指标"""
    from services.market_data_cache import _macro_cache
    from services.macro_sync import sync_macro_indicators

    out = sync_macro_indicators()
    _macro_cache.invalidate()
    return out


@router.get("/macro/score")
async def macro_score():
    """宏观环境评分"""
    from services.macro_sync import get_macro_score
    return get_macro_score()


@router.get("/macro/indicators")
async def macro_indicators(force: bool = Query(False, description="跳过宏观读缓存")):
    """宏观指标历史（读库，10 分钟内存缓存）"""
    import sqlite3
    from services.market_data_cache import cached_macro_indicators

    def _load() -> dict:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM macro_indicators ORDER BY date DESC LIMIT 12"
        ).fetchall()
        conn.close()
        return {"indicators": [dict(r) for r in rows], "cached": False}

    out = cached_macro_indicators(force, _load)
    if not force:
        out = dict(out)
        out["cached"] = True
    return out
