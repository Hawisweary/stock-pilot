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
    skip_tushare_events: bool = False
    tushare_event_days: int = Field(10, ge=1, le=365)
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
        skip_tushare_events=body.skip_tushare_events,
        tushare_event_days=body.tushare_event_days,
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


@router.get("/market-scopes")
async def v5_market_scopes():
    """V5 批量查询可用的市场 / 板块 scope 列表。"""
    from services.market_filter import MARKET_SCOPES

    return {
        "scopes": [{"id": k, "label": v} for k, v in MARKET_SCOPES.items()],
    }


@router.get("/scores/batch")
async def v5_scores_batch(
    limit: int | None = Query(None, ge=1, le=10000),
    scope: str | None = Query(None, description="ALL/A/SH/SZ/STAR/CHINEXT/MAIN_SH/MAIN_SZ/SME/BJ"),
    market: str | None = Query(None, description="兼容旧参数，等同 scope"),
):
    """全市场 V5 分数批量查询（与股票列表同一 JOIN 规则）。"""
    from database import get as get_db_conn
    from services.market_filter import normalize_scope, scope_label, scope_sql

    conn = get_db_conn()
    resolved = normalize_scope(scope, market=market)
    scope_clause, scope_params = scope_sql(
        resolved, market_col="market", code_col="code"
    )
    sql = f"""
        SELECT stock_id, code, name, industry_sw, market,
               calc_date, score AS composite_v5, veto_status
        FROM v_stock_scores
        WHERE score IS NOT NULL
        {scope_clause}
    """
    params: list = list(scope_params)
    sql += " ORDER BY score DESC"
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    calc_dates = [r.get("calc_date") for r in rows if r.get("calc_date")]
    return {
        "scope": resolved,
        "scope_label": scope_label(resolved),
        "calc_date": max(calc_dates) if calc_dates else None,
        "scores": rows,
        "count": len(rows),
    }


@router.get("/stocks-with-scores")
async def stocks_with_scores(
    scope: str | None = Query(None, description="ALL/A/SH/SZ/STAR/CHINEXT/MAIN_SH/MAIN_SZ/SME/BJ"),
    market: str | None = Query(None, description="兼容旧参数，等同 scope"),
):
    """股票列表 + V5 分数合并接口（单次查询，替代前端两次并发请求）。"""
    from database import get as get_db_conn
    from services.market_filter import normalize_scope, scope_label, scope_sql

    conn = get_db_conn()
    resolved = normalize_scope(scope, market=market)
    scope_clause, scope_params = scope_sql(
        resolved, market_col="s.market", code_col="s.code"
    )
    sql = f"""
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
        {scope_clause}
    """
    params: list = list(scope_params)
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
        "scope": resolved,
        "scope_label": scope_label(resolved),
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


@router.get("/data-quality/summary")
async def data_quality_summary(trade_date: str | None = None):
    """按交易日获取数据质量异常摘要（总数量、分级、TOP20）。"""
    from database import get as get_db_conn
    from services.data_quality import get_summary_for_date

    conn = get_db_conn()
    return get_summary_for_date(conn, trade_date)


@router.get("/data-quality/stock/{stock_id}")
async def data_quality_for_stock(stock_id: int, limit: int = Query(30, ge=1, le=200)):
    """获取某只股票的历史数据质量告警。"""
    from database import get as get_db_conn
    from services.data_quality import get_alerts_for_stock

    conn = get_db_conn()
    return {"stock_id": stock_id, "alerts": get_alerts_for_stock(conn, stock_id, limit)}


@router.post("/data-quality/detect")
async def data_quality_detect(trade_date: str | None = None):
    """手动触发某交易日的数据质量检测（幂等）。"""
    from database import get as get_db_conn
    from services.data_quality import detect_and_write

    conn = get_db_conn()
    return detect_and_write(conn, trade_date=trade_date)


@router.get("/market-regime")
async def market_regime(trade_date: str | None = None):
    """获取市场状态分类（双轨：CSI300 + CSI800）。"""
    from database import get as get_db_conn
    from services.market_regime import get_regime_for_date, sync_regime

    conn = get_db_conn()
    row = get_regime_for_date(conn, trade_date)
    if row.get("regime") and (row.get("regime_csi800") or row.get("indices")):
        return row
    return sync_regime(conn, trade_date=trade_date)


@router.post("/market-regime/sync")
async def market_regime_sync(trade_date: str | None = None):
    """手动触发市场状态双轨分类检测。"""
    from database import get as get_db_conn
    from services.market_regime import sync_regime

    conn = get_db_conn()
    return sync_regime(conn, trade_date=trade_date)


@router.get("/market-regime/agreement-stats")
async def market_regime_agreement_stats(days: int = 252):
    """CSI300 vs CSI800 历史标签一致率。"""
    from database import get as get_db_conn
    from services.market_regime import get_regime_agreement_stats

    conn = get_db_conn()
    return get_regime_agreement_stats(conn, days=max(30, min(days, 730)))


@router.get("/market-regime/history")
async def market_regime_history(
    days: int = 730,
    primary: str = "csi800",
):
    """L1 四格状态历史序列（周期可视化）。"""
    from database import get as get_db_conn
    from services.market_regime import get_regime_history

    conn = get_db_conn()
    return get_regime_history(
        conn,
        primary=primary if primary in ("csi800", "csi300") else "csi800",
        days=max(30, min(days, 730)),
    )


@router.post("/market-regime/recompute-persistence")
async def market_regime_recompute_persistence(
    days: int = 730,
    persistence_days: int | None = None,
):
    """从 raw 日频快照重算持续性确认状态。"""
    from database import get as get_db_conn
    from services.market_regime import recompute_regime_persistence

    conn = get_db_conn()
    return recompute_regime_persistence(
        conn,
        days=max(30, min(days, 730)),
        min_days=persistence_days,
    )


@router.get("/market-regime/validation")
async def market_regime_validation(
    primary: str = "csi800",
    days: int = 365,
    include_strategy: bool = False,
    strategy_days: int = 180,
    include_l3_sim: bool = False,
    l3_sim_days: int = 365,
):
    """市场状态划分三层验证报告（内部一致性 / Walk-Forward / 可选策略条件 / L3 切换模拟）。"""
    from database import get as get_db_conn
    from services.regime_validation import generate_validation_report

    if primary not in ("csi300", "csi800"):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="primary 须为 csi300 或 csi800")
    conn = get_db_conn()
    return generate_validation_report(
        conn,
        primary=primary,
        days=max(60, min(days, 730)),
        include_strategy=include_strategy,
        strategy_days=max(90, min(strategy_days, 365)),
        include_l3_sim=include_l3_sim,
        l3_sim_days=max(90, min(l3_sim_days, 730)),
    )


@router.get("/strategy-regime-matrix")
async def strategy_regime_matrix(auto_refresh: bool = False):
    """L2：策略×四格状态绩效矩阵 + 当前推荐。"""
    from database import get as get_db_conn
    from services.strategy_regime_performance import get_strategy_regime_matrix

    conn = get_db_conn()
    return get_strategy_regime_matrix(conn, auto_refresh=auto_refresh)


@router.post("/strategy-regime-matrix/refresh")
async def strategy_regime_matrix_refresh(
    lookback_days: int | None = None,
    backtest_days: int | None = None,
):
    """重算 L2 矩阵（回测 + 模拟盘）。"""
    import config
    from database import get as get_db_conn
    from services.strategy_regime_performance import refresh_strategy_regime_matrix

    lb = lookback_days if lookback_days is not None else config.REGIME_MATRIX_LOOKBACK_DAYS
    bt = backtest_days if backtest_days is not None else config.REGIME_MATRIX_BACKTEST_DAYS
    conn = get_db_conn()
    return refresh_strategy_regime_matrix(
        conn,
        lookback_days=max(60, min(lb, 730)),
        backtest_days=max(90, min(bt, 730)),
    )


@router.get("/strategy-regime-matrix/drilldown/{strategy_id}")
async def strategy_regime_drilldown(strategy_id: str, backtest_days: int = 180):
    """七格 drill-down（单策略）。"""
    from database import get as get_db_conn
    from services.strategy_regime_performance import build_drilldown_7

    conn = get_db_conn()
    return {
        "strategy": strategy_id,
        "cells": build_drilldown_7(
            conn, strategy_id, backtest_days=max(90, min(backtest_days, 365)),
        ),
    }


@router.get("/recommendations/current")
async def recommendations_current(refresh: bool = False):
    """L3：当前策略推荐（市场状态 + 矩阵 + 选股预览）。"""
    from database import get as get_db_conn
    from services.strategy_recommender import generate_current_recommendation, get_current_recommendation

    conn = get_db_conn()
    if refresh:
        return generate_current_recommendation(conn, refresh_matrix=False, persist=True)
    return get_current_recommendation(conn)


@router.post("/recommendations/generate")
async def recommendations_generate(refresh_matrix: bool = False):
    """手动生成并落库 L3 推荐。"""
    from database import get as get_db_conn
    from services.strategy_recommender import generate_current_recommendation

    conn = get_db_conn()
    return generate_current_recommendation(conn, refresh_matrix=refresh_matrix, persist=True)


@router.get("/recommendations/monitoring")
async def recommendations_monitoring(days: int = 365):
    """P1：推荐命中率 + regime 切换摘要。"""
    from database import get as get_db_conn
    from services.strategy_recommendation_monitor import get_monitoring_dashboard

    conn = get_db_conn()
    return get_monitoring_dashboard(conn, days=max(30, min(days, 730)))


@router.get("/recommendations/switches")
async def recommendations_switches(limit: int = 30):
    """P1：regime / 策略切换日志。"""
    from database import get as get_db_conn
    from services.strategy_recommendation_monitor import get_recent_switches

    conn = get_db_conn()
    return {"switches": get_recent_switches(conn, limit=max(1, min(limit, 100)))}


@router.get("/market-regime/layers")
async def market_regime_layers(trade_date: str | None = None):
    """规则 / Jump / HMM 并列四格标签（Dashboard 对照条）。"""
    from database import get as get_db_conn
    from services.market_regime import get_regime_layers_for_date

    conn = get_db_conn()
    return get_regime_layers_for_date(conn, trade_date)


@router.get("/market-regime/hmm/compare")
async def market_regime_hmm_compare(days: int = 730, persist: bool = False):
    """P3-C：HMM vs 规则 L1 对照（可选落库）。"""
    from database import get as get_db_conn
    from services.regime_hmm import compare_hmm_vs_rules, fit_and_persist_full_sample

    conn = get_db_conn()
    report = compare_hmm_vs_rules(conn, days=max(90, min(days, 730)))
    if persist and not report.get("error"):
        report["persist"] = fit_and_persist_full_sample(conn, days=days)
    return report


@router.get("/market-regime/cluster/compare")
async def market_regime_cluster_compare(
    days: int = 730,
    persist: bool = False,
    method: str = "both",
):
    """P3-D：K-Means / GMM vs 规则 L1 对照（可选落库）。"""
    from database import get as get_db_conn
    from services.regime_cluster import (
        compare_cluster_vs_rules,
        fit_and_persist_full_sample,
    )

    methods = ("kmeans", "gmm") if method == "both" else (method,)
    conn = get_db_conn()
    report = compare_cluster_vs_rules(conn, days=max(90, min(days, 730)), methods=methods)
    if persist and not report.get("error"):
        report["persist"] = fit_and_persist_full_sample(conn, days=days, methods=methods)
    return report


@router.get("/market-regime/jump/compare")
async def market_regime_jump_compare(
    days: int = 730,
    persist: bool = False,
    penalties: str = "25,50,75,100",
    backend: str = "auto",
):
    """P3-E：Jump Model vs 规则 L1 对照（λ 扫描，可选落库）。"""
    from database import get as get_db_conn
    from services.regime_jump import (
        compare_jump_vs_rules,
        fit_and_persist_full_sample,
    )

    lam_tuple = tuple(float(x.strip()) for x in penalties.split(",") if x.strip())
    conn = get_db_conn()
    report = compare_jump_vs_rules(
        conn,
        days=max(90, min(days, 730)),
        penalties=lam_tuple or (25.0, 50.0, 75.0, 100.0),
        backend=backend if backend in ("auto", "jumpmodels", "simple") else "auto",
    )
    if persist and not report.get("error"):
        lam = report.get("recommended_penalty") or 50.0
        report["persist"] = fit_and_persist_full_sample(
            conn, days=days, jump_penalty=float(lam), backend=backend if backend in ("auto", "jumpmodels", "simple") else "auto",
        )
    return report


@router.post("/regime-pipeline/run")
async def regime_pipeline_run(
    refresh_matrix: bool = True,
    skip_regime: bool = False,
    lookback_days: int | None = None,
    backtest_days: int | None = None,
):
    """手动触发 L1→L2→L3 流水线（运维/补跑）。"""
    import config
    from database import get as get_db_conn
    from services.regime_pipeline import run_regime_l2_l3_pipeline

    conn = get_db_conn()
    return run_regime_l2_l3_pipeline(
        conn,
        skip_regime=skip_regime,
        refresh_matrix=refresh_matrix,
        lookback_days=lookback_days or config.REGIME_MATRIX_LOOKBACK_DAYS,
        backtest_days=backtest_days or config.REGIME_MATRIX_BACKTEST_DAYS,
    )


@router.get("/volatility-forecast")
async def volatility_forecast_summary(trade_date: str | None = None):
    """获取波动率 / 流动性预测摘要（平均值、TOP20 高波动）。"""
    from database import get as get_db_conn
    from services.volatility_forecast import get_summary_for_date, sync_forecast

    conn = get_db_conn()
    if trade_date:
        row = get_summary_for_date(conn, trade_date)
        if row.get("total_records"):
            return row
    return sync_forecast(conn, trade_date=trade_date)


@router.get("/volatility-forecast/{stock_id}")
async def volatility_forecast_stock(stock_id: int, limit: int = Query(30, ge=1, le=365)):
    """获取某只股票的历史波动率 / 流动性预测。"""
    from database import get as get_db_conn
    from services.volatility_forecast import get_forecast_for_stock

    conn = get_db_conn()
    return {"stock_id": stock_id, "forecasts": get_forecast_for_stock(conn, stock_id, limit)}


@router.post("/volatility-forecast/sync")
async def volatility_forecast_sync(trade_date: str | None = None):
    """手动触发波动率 / 流动性预测计算。"""
    from database import get as get_db_conn
    from services.volatility_forecast import sync_forecast

    conn = get_db_conn()
    return sync_forecast(conn, trade_date=trade_date)
