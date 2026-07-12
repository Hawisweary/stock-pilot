"""V5 扩展数据源 API。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from api_utils import execute_sql

router = APIRouter(prefix="/api/v5", tags=["v5-data"])


class V5SyncBody(BaseModel):
    stock_ids: list[int] | None = None
    mode: str | None = Field(
        None,
        description="预设：daily | nightly | weekly（EPS/宏观/主力流）| 不传=custom",
    )
    skip_macro: bool = False
    skip_fund_flow: bool = False
    skip_sector: bool = False
    skip_metrics: bool = False
    skip_industry_l2: bool = False
    skip_eps_revision: bool = False
    skip_announcements: bool = False
    skip_news_fetch: bool = False
    skip_events: bool = False
    reclassify_events: bool = True
    use_llm_events: bool = True
    llm_only_missing_news: bool = False
    llm_event_limit_per_stock: int = Field(12, ge=3, le=30)
    skip_risk: bool = False
    skip_policy: bool = False
    skip_mood: bool = False
    skip_v5_scores: bool = False
    announcement_limit: int = Field(30, ge=5, le=80)
    news_limit: int = Field(15, ge=5, le=50)


@router.post("/sync")
async def sync_v5(body: V5SyncBody | None = None):
    """一键同步 V5 数据源（宏观/资金流/质量/EPS/事件/风险/政策/情绪）。

    推荐日常：`{"mode":"daily"}` — 跳过抓取，仅缺新闻面股票跑 LLM。
    夜间抓取：`{"mode":"nightly"}` — 公告/新闻入库 + 全量分类，不重算分数。
    """
    from services.v5_data_sync import V5_SYNC_MODE_PRESETS, sync_v5_data_sources

    body = body or V5SyncBody()
    if body.mode and body.mode not in V5_SYNC_MODE_PRESETS:
        raise HTTPException(
            status_code=400,
            detail=f"mode 需为 {list(V5_SYNC_MODE_PRESETS.keys())} 之一",
        )
    return sync_v5_data_sources(
        stock_ids=body.stock_ids,
        mode=body.mode,
        skip_macro=body.skip_macro,
        skip_fund_flow=body.skip_fund_flow,
        skip_sector=body.skip_sector,
        skip_metrics=body.skip_metrics,
        skip_industry_l2=body.skip_industry_l2,
        skip_eps_revision=body.skip_eps_revision,
        skip_announcements=body.skip_announcements,
        skip_news_fetch=body.skip_news_fetch,
        skip_events=body.skip_events,
        reclassify_events=body.reclassify_events,
        use_llm_events=body.use_llm_events,
        llm_only_missing_news=body.llm_only_missing_news,
        llm_event_limit_per_stock=body.llm_event_limit_per_stock,
        skip_risk=body.skip_risk,
        skip_policy=body.skip_policy,
        skip_mood=body.skip_mood,
        skip_v5_scores=body.skip_v5_scores,
        announcement_limit=body.announcement_limit,
        news_limit=body.news_limit,
    )


@router.get("/metrics/{stock_id}")
async def get_v5_metrics(stock_id: int):
    from services.quality_metrics_calc import get_stock_v5_metrics

    stock = execute_sql("SELECT id FROM stocks WHERE id=? AND is_active=1", (stock_id,))
    if not stock:
        raise HTTPException(status_code=404, detail="股票不存在")
    m = get_stock_v5_metrics(stock_id)
    return {"stock_id": stock_id, "metrics": m}


@router.get("/fund-flow/{stock_id}")
async def get_fund_flow(stock_id: int, days: int = Query(20, ge=5, le=60)):
    from services.fund_flow_sync import get_stock_fund_flow

    stock = execute_sql("SELECT id FROM stocks WHERE id=? AND is_active=1", (stock_id,))
    if not stock:
        raise HTTPException(status_code=404, detail="股票不存在")
    return get_stock_fund_flow(stock_id, days=days)


@router.get("/sector-fund-flow")
async def sector_fund_flow(limit: int = Query(30, ge=5, le=100)):
    from services.sector_fund_flow_sync import get_sector_fund_flow

    return get_sector_fund_flow(limit=limit)


@router.get("/industry-eps-revision")
async def industry_eps_revision(
    industry: str | None = Query(None, description="申万二级行业名"),
    limit: int = Query(30, ge=5, le=100),
):
    from services.eastmoney_forecast_sync import get_industry_eps_revision

    rows = get_industry_eps_revision(industry, limit=limit)
    return {"items": rows, "count": len(rows)}


@router.get("/eps-forecast/{stock_id}")
async def eps_forecast(stock_id: int):
    from services.eastmoney_forecast_sync import get_stock_eps_forecast

    stock = execute_sql("SELECT id FROM stocks WHERE id=? AND is_active=1", (stock_id,))
    if not stock:
        raise HTTPException(status_code=404, detail="股票不存在")
    row = get_stock_eps_forecast(stock_id)
    return {"stock_id": stock_id, "forecast": row}


@router.get("/risk-flags/{stock_id}")
async def risk_flags(stock_id: int, limit: int = Query(20, ge=1, le=100)):
    from services.risk_scanner import get_risk_flags, has_veto_risk

    stock = execute_sql("SELECT id FROM stocks WHERE id=? AND is_active=1", (stock_id,))
    if not stock:
        raise HTTPException(status_code=404, detail="股票不存在")
    flags = get_risk_flags(stock_id, limit=limit)
    return {
        "stock_id": stock_id,
        "flags": flags,
        "has_veto_risk": has_veto_risk(stock_id),
    }


@router.get("/events/{stock_id}")
async def stock_events(
    stock_id: int,
    limit: int = Query(20, ge=5, le=50),
    include_fundamental: bool = Query(False),
):
    from services.event_classifier import get_stock_events

    stock = execute_sql("SELECT id FROM stocks WHERE id=? AND is_active=1", (stock_id,))
    if not stock:
        raise HTTPException(status_code=404, detail="股票不存在")
    events = get_stock_events(
        stock_id, limit=limit, include_fundamental=include_fundamental
    )
    return {"stock_id": stock_id, "events": events}


@router.get("/policy-events")
async def policy_events(limit: int = Query(30, ge=5, le=100)):
    from services.policy_event_sync import get_policy_events

    rows = get_policy_events(limit=limit)
    return {"items": rows, "count": len(rows)}


@router.get("/policy-score/{stock_id}")
async def policy_score_v5(stock_id: int):
    from services.policy_event_sync import get_policy_score_v5_for_stock

    stock = execute_sql("SELECT id FROM stocks WHERE id=? AND is_active=1", (stock_id,))
    if not stock:
        raise HTTPException(status_code=404, detail="股票不存在")
    row = get_policy_score_v5_for_stock(stock_id)
    return row or {"stock_id": stock_id, "policy_score_v5": 0, "tier": 0, "events": []}


@router.get("/mood/{stock_id}")
async def mood_v5(stock_id: int):
    from services.mood_scorer import get_stock_mood_v5

    stock = execute_sql("SELECT id FROM stocks WHERE id=? AND is_active=1", (stock_id,))
    if not stock:
        raise HTTPException(status_code=404, detail="股票不存在")
    m = get_stock_mood_v5(stock_id)
    return {"stock_id": stock_id, "mood": m}


@router.get("/heatmap")
async def v5_heatmap():
    """V5 十维档位热力图（每只股票最新 calc_date）。"""
    import json

    from database import get as get_db_conn
    from services.score_sql import per_stock_latest_join
    from services.v5_scorer import V5_LABELS, tier_to_pct

    dim_order = list(V5_LABELS.keys())
    labels = [V5_LABELS[k] for k in dim_order] + ["V5综合"]

    conn = get_db_conn()
    join_cs = per_stock_latest_join("cs")
    rows = conn.execute(
        f"""
        SELECT s.id AS stock_id, s.code, s.name, s.industry_sw AS industry,
               cs.composite_v5, cs.v5_breakdown_json
        FROM stocks s
        {join_cs}
        WHERE s.is_active=1 AND cs.composite_v5 IS NOT NULL
        ORDER BY cs.composite_v5 DESC
        """,
    ).fetchall()

    matrix = []
    for r in rows:
        row = {
            "stock_id": r["stock_id"],
            "code": r["code"],
            "name": r["name"],
            "industry": r["industry"],
        }
        tiers: dict[str, int | None] = {}
        raw = r["v5_breakdown_json"]
        if raw:
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
                tiers = (parsed or {}).get("tiers") or {}
            except (json.JSONDecodeError, TypeError):
                tiers = {}
        for key, label in zip(dim_order, labels[:-1]):
            t = tiers.get(key)
            row[label] = tier_to_pct(t) if t is not None else None
        row["V5综合"] = float(r["composite_v5"]) if r["composite_v5"] is not None else None
        matrix.append(row)

    return {"dims": labels, "matrix": matrix, "count": len(matrix)}


@router.get("/scores/batch")
async def v5_scores_batch(
    limit: int | None = Query(None, ge=1, le=10000),
    market: str | None = Query(None, description="SH/SZ/A/HK/US/ALL，ALL 或不传返回全部"),
):
    """全市场 V5 分数批量查询（与股票列表同一 JOIN 规则）。"""
    from database import get as get_db_conn

    conn = get_db_conn()
    # v3.0: 使用 v_stock_scores 视图，省去手动 latest-JOIN
    sql = """
        SELECT stock_id, code, name, industry_sw, market,
               calc_date, score AS composite_v5, veto_status
        FROM v_stock_scores
        WHERE score IS NOT NULL
    """
    params: list = []
    if market and market.upper().strip() not in ("ALL", ""):
        sql += " AND market=?"
        params.append(market.upper().strip())
    sql += " ORDER BY score DESC"
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    calc_dates = [r.get("calc_date") for r in rows if r.get("calc_date")]
    return {
        "calc_date": max(calc_dates) if calc_dates else None,
        "scores": rows,
        "count": len(rows),
    }


@router.get("/stocks-with-scores")
async def stocks_with_scores(
    market: str | None = Query(None, description="A/SH/SZ/HK/US/ALL，不传返回全部活跃股票"),
):
    """股票列表 + V5 分数合并接口（单次查询，替代前端两次并发请求）。"""
    from database import get as get_db_conn

    conn = get_db_conn()
    # 直接走 comprehensive_scores_latest 影子表点查，避免 v_stock_scores 视图内部
    # 已 JOIN 过 stocks 之后，这里再对 stocks 做一次冗余 self-join。
    sql = """
        SELECT
            s.id, s.code, s.name, s.market, s.sector, s.industry,
            s.industry_sw, s.industry_sw2, s.industry_sw3, s.is_active, s.list_date,
            cs.calc_date, cs.composite_v5, cs.veto_status,
            cs.fundamental_score, cs.technical_score, cs.sentiment_score,
            cs.capital_score, cs.policy_score, cs.mood_score,
            cs.val_score, cs.quality_score, cs.industry_score, cs.market_env_score
        FROM stocks s
        LEFT JOIN comprehensive_scores_latest l ON s.id = l.stock_id
        LEFT JOIN comprehensive_scores cs ON cs.id = l.cs_id
        WHERE s.is_active = 1
    """
    params: list = []
    if market and market.upper().strip() not in ("ALL", ""):
        sql += " AND s.market=?"
        params.append(market.upper().strip())
    sql += " ORDER BY cs.composite_v5 DESC NULLS LAST"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    concept_map: dict[int, list[str]] = {}
    for stock_id, name in conn.execute(
        "SELECT stock_id, name FROM stock_concept_boards GROUP BY stock_id, name"
    ).fetchall():
        concept_map.setdefault(stock_id, []).append(name)
    for r in rows:
        r["concepts"] = concept_map.get(r["id"], [])

    calc_dates = [r.get("calc_date") for r in rows if r.get("calc_date")]
    return {
        "calc_date": max(calc_dates) if calc_dates else None,
        "rows": rows,
        "count": len(rows),
    }


@router.get("/scores/{stock_id}")
async def v5_scores(stock_id: int):
    from services.v5_scorer import get_stock_v5_score

    stock = execute_sql("SELECT id FROM stocks WHERE id=? AND is_active=1", (stock_id,))
    if not stock:
        raise HTTPException(status_code=404, detail="股票不存在")
    data = get_stock_v5_score(stock_id)
    if not data:
        raise HTTPException(status_code=404, detail="V5 分数不可用")
    return {"stock_id": stock_id, "v5": data}


@router.post("/compute-scores")
async def compute_v5_scores(body: V5SyncBody | None = None):
    from services.v5_scorer import compute_all_v5_scores

    body = body or V5SyncBody()
    return compute_all_v5_scores(body.stock_ids)


@router.get("/valuation-percentile/{stock_id}")
async def valuation_percentile(stock_id: int):
    """
    返回单股最新 PE/PB 及其行业内百分位（P1-2）。
    读取 valuation_scores.breakdown_json 中已计算好的 pe_pct / pb_pct。
    """
    import json as _json
    from database import get as get_db_conn

    conn = get_db_conn()
    row = conn.execute(
        """
        SELECT vs.date, vs.composite_score AS val_score,
               vs.pe_score, vs.pb_score, vs.breakdown_json,
               s.industry_sw, s.name, s.code
        FROM valuation_scores vs
        JOIN stocks s ON s.id = vs.stock_id
        WHERE vs.stock_id = ?
        ORDER BY vs.date DESC LIMIT 1
        """,
        (stock_id,),
    ).fetchone()
    # 今日实时快照（腾讯行情）
    snap = conn.execute(
        """
        SELECT pe_ttm, pb, market_cap, as_of_date
        FROM valuation_snapshots
        WHERE stock_id = ?
        ORDER BY as_of_date DESC LIMIT 1
        """,
        (stock_id,),
    ).fetchone()

    if not row:
        return {"available": False}

    bd = {}
    try:
        bd = _json.loads(row["breakdown_json"] or "{}")
    except Exception:
        pass

    pe = bd.get("pe")
    pb = bd.get("pb")
    industry = bd.get("industry") or row["industry_sw"]
    industry_n = bd.get("industry_sample_n")

    pe_pct_cheap = row["pe_score"]
    pb_pct_cheap = row["pb_score"]

    result = {
        "available": True,
        "date": row["date"],
        "code": row["code"],
        "name": row["name"],
        "industry": industry,
        "industry_sample_n": industry_n,
        "val_score": row["val_score"],
        "pe": pe,
        "pb": pb,
        "pe_cheap_pct": round(pe_pct_cheap, 1) if pe_pct_cheap is not None else None,
        "pb_cheap_pct": round(pb_pct_cheap, 1) if pb_pct_cheap is not None else None,
    }
    # 补充今日实时 PE/PB（来自腾讯行情，可能更新）
    if snap:
        result["live_pe"] = snap["pe_ttm"] or None
        result["live_pb"] = snap["pb"] or None
        result["live_market_cap"] = snap["market_cap"]
        result["live_date"] = snap["as_of_date"]
    return result


@router.get("/ic-report")
async def v5_ic_report(dimension: str = Query("composite_v5")):
    from services.ic_engine import compute_v5_dimension_ic

    allowed = {"composite_v5", "quality_score", "industry_score", "market_env_score"}
    if dimension not in allowed:
        raise HTTPException(status_code=400, detail=f"dimension 需为 {allowed}")
    return compute_v5_dimension_ic(dimension)


class BulkScoreBody(BaseModel):
    stock_ids: list[int] = Field(..., min_length=1, max_length=200)


@router.post("/scores/bulk")
async def v5_scores_bulk(body: BulkScoreBody):
    """批量获取 V5 评分（含10维度分），用于分组对比视图（U2-2）。"""
    from database import get as get_db_conn

    ids = list(dict.fromkeys(int(i) for i in body.stock_ids))
    ph = ",".join("?" * len(ids))
    conn = get_db_conn()
    rows = conn.execute(
        f"""
        SELECT stock_id, code, name, industry_sw, calc_date,
               score AS composite_v5, veto_status,
               fundamental_score, technical_score, sentiment_score,
               capital_score, policy_score, mood_score, val_score,
               quality_score, industry_score, market_env_score
        FROM v_stock_scores
        WHERE stock_id IN ({ph})
        """,
        ids,
    ).fetchall()
    return {"scores": [dict(r) for r in rows], "count": len(rows)}
