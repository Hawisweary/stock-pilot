import config
from config import DB_PATH, KLINE_DISPLAY_DAYS, SCORING_MODE

"""
股票管理 API
- GET  /api/stocks         列出所有跟踪股票
- POST /api/stocks         添加股票到跟踪列表
- GET  /api/stocks/{id}    获取股票详情+最新评分
- DELETE /api/stocks/{id}  删除股票
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
from fastapi import Request
from api_utils import execute_sql, execute_insert, execute_update
from api_models import StockIn, StockOut, StockWithScores, _ALLOWED_MARKETS, _CODE_RE
from rate_limit import rate_limit_write, rate_limit_onboard
from services.score_sql import per_stock_latest_join, per_stock_latest_v5_join

router = APIRouter(prefix="/api", tags=["stocks"])


@router.get("/stocks")
async def list_stocks(market: str = "ALL"):
    """列出所有跟踪股票（含行业）。market=ALL 返回全部活跃股票。"""
    market = market.upper().strip()
    allowed_with_all = _ALLOWED_MARKETS | {"ALL", "SH", "SZ"}
    if market not in allowed_with_all:
        raise HTTPException(status_code=400, detail=f"market 必须是 {sorted(allowed_with_all)}")
    join_cs = per_stock_latest_join("cs")
    join_v5 = per_stock_latest_v5_join("cv5")
    if market == "ALL":
        where_clause = "WHERE s.is_active=1"
        params: tuple = ()
    else:
        where_clause = "WHERE s.market=? AND s.is_active=1"
        params = (market,)
    rows = execute_sql(
        f"""SELECT s.id, s.code, s.name, s.market, s.sector,
                  s.is_active, s.created_at, s.updated_at,
                  s.industry, s.industry_sw,
                  GROUP_CONCAT(it.name, ',') as industry_tags,
                  cs.composite_score,
                  cs.fundamental_score, cs.technical_score,
                  cs.sentiment_score as news_score,
                  cs.capital_score, cs.policy_score,
                  cs.mood_score, cs.val_score,
                  cv5.composite_v5, cv5.veto_status
           FROM stocks s
           LEFT JOIN stock_industries si ON s.id=si.stock_id
           LEFT JOIN industry_tags it ON si.industry_id=it.id
           {join_cs}
           {join_v5}
           {where_clause}
           GROUP BY s.id ORDER BY s.code""",
        params,
    )
    for r in rows:
        # v3.0 双轨：score = composite_v5（权威）；兼容期保留 composite_score 只读
        r["score"] = r.get("composite_v5")
        if SCORING_MODE != "v5_only":
            r["_deprecated"] = {
                "composite_score": "use score/composite_v5",
                "final_score": "removed in v3.0",
            }
        else:
            r.pop("composite_score", None)
        tags = r.pop("industry_tags", "") or ""
        r["industry_list"] = [t.strip() for t in tags.split(",") if t.strip()]
        raw_ind = r.pop("industry", "") or ""
        sw_ind = r.pop("industry_sw", "") or ""

        # 去重：industry_sw 是 industry 的简单变形则跳过
        def _is_trivial(orig: str, sw: str) -> bool:
            return orig.replace(" ", "").replace("&", "").replace("-","").lower() == sw.replace(" ", "").replace("&", "").replace("-","").lower()

        from services.industry_normalize import normalize_industry
        if raw_ind and raw_ind not in r["industry_list"]:
            r["industry_list"].append(raw_ind)

        if sw_ind and not _is_trivial(raw_ind, sw_ind):
            if sw_ind not in r["industry_list"]:
                r["industry_list"].append(sw_ind)

        # 英文名 → 中文映射（统一双语显示）
        cn = normalize_industry(raw_ind)
        if cn and cn != raw_ind and cn not in r["industry_list"]:
            r["industry_list"].append(cn)

        r["industry"] = raw_ind or (r["industry_list"][0] if r["industry_list"] else "")
    return rows


@router.post("/stocks")
async def add_stock(request: Request, body: StockIn):
    """添加股票到跟踪列表，自动触发 fetch_job + 可选 batch-fill"""
    rate_limit_write(request)
    from services.onboard_service import register_stock

    reg = register_stock(body.code, body.market, skip_existing=True)
    if reg.get("status") == "skipped":
        raise HTTPException(status_code=409, detail=f"股票 {body.code} 已在跟踪列表中")
    if reg.get("status") not in ("added", "reactivated", "exists") or not reg.get("stock_id"):
        raise HTTPException(status_code=400, detail=reg.get("reason", "添加失败"))

    stock_id = reg["stock_id"]
    rows = execute_sql("SELECT * FROM stocks WHERE id=?", (stock_id,))
    result = rows[0] if rows else {}

    if config.ONBOARD_AUTO:
        import asyncio
        from services import fetch_job

        fetch_job.reset_stale_jobs()
        if not fetch_job.is_running(stock_id):
            fetch_job.start_job(stock_id)
            asyncio.create_task(
                fetch_job.run_single_fetch_async(stock_id, body.code, body.market)
            )
            result = dict(result)
            result["fetch_status"] = "started"
            result["fetch_poll_url"] = f"/api/data/fetch/{stock_id}/status"

    return result


class OnboardBody(BaseModel):
    codes: list[str] = Field(..., min_length=1, max_length=50)
    market: str = Field(default="A")
    auto_score: bool = True
    score_mode: str | None = None
    skip_existing: bool = True
    fetch_parallel: int | None = None

    @field_validator("market")
    @classmethod
    def market_whitelist(cls, v: str) -> str:
        v = v.upper().strip()
        if v not in _ALLOWED_MARKETS:
            raise ValueError(f"market 必须是 {sorted(_ALLOWED_MARKETS)}")
        return v

    @field_validator("codes", mode="before")
    @classmethod
    def codes_safe(cls, v: list) -> list:
        for c in v:
            if not _CODE_RE.match(str(c).strip()):
                raise ValueError(f"无效代码格式: {c}")
        return v


@router.post("/stocks/onboard")
async def onboard_stocks(request: Request, body: OnboardBody):
    """一键 onboard：register → prefetch → fetch → factor → batch-fill"""
    rate_limit_onboard(request)
    from services.onboard_service import enqueue_onboard, register_stocks

    registered = register_stocks(body.codes, body.market, skip_existing=body.skip_existing)
    stock_ids = [r["stock_id"] for r in registered if r.get("stock_id")]
    if not stock_ids:
        return {"ok": False, "message": "无有效股票", "registered": registered}

    payload = {
        "market": body.market,
        "stock_ids": stock_ids,
        "registered": registered,
        "auto_score": body.auto_score,
        "score_mode": body.score_mode or config.ONBOARD_SCORE_MODE,
        "fetch_parallel": body.fetch_parallel or config.FETCH_PARALLEL,
        "skip_existing": body.skip_existing,
    }
    job = enqueue_onboard(payload)
    return {
        "ok": True,
        "job_id": job.id,
        "poll_url": f"/api/system/jobs/{job.id}",
        "registered": registered,
        "stock_ids": stock_ids,
    }


class BatchStockIn(BaseModel):
    codes: list[str] = Field(..., min_length=1, max_length=50, description="股票代码列表，如 ['600519','000858']")
    market: str = Field(default="A", description="市场，默认A股")

    @field_validator("market")
    @classmethod
    def market_whitelist(cls, v: str) -> str:
        v = v.upper().strip()
        if v not in _ALLOWED_MARKETS:
            raise ValueError(f"market 必须是 {sorted(_ALLOWED_MARKETS)}")
        return v

    @field_validator("codes", mode="before")
    @classmethod
    def codes_safe(cls, v: list) -> list:
        for c in v:
            if not _CODE_RE.match(str(c).strip()):
                raise ValueError(f"无效代码格式: {c}")
        return v


@router.post("/stocks/batch-add")
async def batch_add_stocks(request: Request, body: BatchStockIn):
    """批量添加股票；≤5 只自动 onboard，>5 只仅入库"""
    rate_limit_onboard(request)
    from services.onboard_service import enqueue_onboard, register_stocks

    codes = [c.strip() for c in body.codes if c.strip()]
    registered = register_stocks(codes, body.market, skip_existing=True)
    stock_ids = [
        r["stock_id"]
        for r in registered
        if r.get("stock_id") and r.get("status") in ("added", "reactivated")
    ]

    response: dict = {
        "ok": True,
        "results": registered,
        "total": len(registered),
        "stock_ids": stock_ids,
    }

    n_codes = len(codes)
    if (
        config.BATCH_ADD_AUTO_ONBOARD
        and n_codes <= config.BATCH_ADD_AUTO_ONBOARD_MAX
        and stock_ids
    ):
        job = enqueue_onboard(
            {
                "market": body.market,
                "stock_ids": stock_ids,
                "registered": registered,
                "auto_score": config.AUTO_SCORE_ON_FETCH,
                "score_mode": config.ONBOARD_SCORE_MODE,
                "fetch_parallel": config.FETCH_PARALLEL,
            }
        )
        response["onboard_job_id"] = job.id
        response["onboard_poll_url"] = f"/api/system/jobs/{job.id}"
        response["onboard_auto"] = True
    elif n_codes > config.BATCH_ADD_AUTO_ONBOARD_MAX:
        response["onboard_auto"] = False
        response["onboard_hint"] = "请调用 POST /api/stocks/onboard"
    else:
        response["onboard_auto"] = False

    return response


# ---- 搜索和分组（必须在 {stock_id} 前面，否则路由冲突） ----

@router.get("/stocks/search/by-name")
async def search_stock_by_name(q: str = ""):
    """按名称搜索A股（使用缓存的全量股票列表）"""
    if len(q.strip()) < 1:
        return {"results": [], "count": 0}
    import json, os
    cache_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "stock_list_cache.json")
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            all_stocks = json.load(f)
        results = [s for s in all_stocks if q.strip() in s["name"]][:20]
        return {"results": results, "count": len(results)}
    # fallback: 本地DB
    rows = execute_sql("SELECT code, name FROM stocks WHERE name LIKE ? AND is_active=1 LIMIT 20", (f"%{q}%",))
    return {"results": [dict(r) for r in rows], "count": len(rows)}


@router.get("/stocks/grouped/by-industry")
async def stocks_grouped_by_industry():
    """按行业分组"""
    rows = execute_sql("""
        SELECT s.id, s.code, s.name, s.industry,
               fs.composite_score, fs.profitability_score, fs.growth_score,
               fs.safety_score, fs.value_score
        FROM stocks s
        LEFT JOIN factor_scores fs ON s.id = fs.stock_id
            AND fs.calc_date = (SELECT MAX(calc_date) FROM factor_scores WHERE stock_id = s.id)
        WHERE s.is_active = 1
        ORDER BY s.industry, fs.composite_score DESC
    """)
    groups = {}
    for r in rows:
        r = dict(r)
        ind = r.get("industry") or "未分类"
        if ind not in groups:
            groups[ind] = {"industry": ind, "stocks": [], "count": 0, "avg_score": 0}
        groups[ind]["stocks"].append(r)
        groups[ind]["count"] += 1
    for g in groups.values():
        scores = [s.get("composite_score") or 0 for s in g["stocks"]]
        g["avg_score"] = round(sum(scores) / len(scores), 1) if scores else 0
    result = sorted(groups.values(), key=lambda g: g["avg_score"], reverse=True)
    return {"groups": result, "total_industries": len(result)}


# ---- 单股票 CRUD ----

@router.get("/stocks/{stock_id}")
async def get_stock_detail(stock_id: int):
    """获取股票详情，包含最新评分和指标"""
    stock = execute_sql("SELECT * FROM stocks WHERE id=?", (stock_id,))
    if not stock:
        raise HTTPException(status_code=404, detail="股票不存在")

    result = dict(stock[0])

    # 获取最新评分
    scores = execute_sql(
        """SELECT * FROM factor_scores
           WHERE stock_id=? ORDER BY calc_date DESC LIMIT 1""",
        (stock_id,)
    )
    result["latest_scores"] = scores[0] if scores else None

    # 获取最新指标
    indicators = execute_sql(
        """SELECT * FROM financial_indicators
           WHERE stock_id=? ORDER BY calc_date DESC LIMIT 1""",
        (stock_id,)
    )
    result["latest_indicators"] = indicators[0] if indicators else None

    # 估值快照（PE/PB/市值 存在单独表，merge 到 indicators）。
    # 同一股票可能同时有 tushare（字段全）和 tencent（仅基础字段）两路写入，
    # 后者可能日期更新但字段更稀疏——优先取字段最全的那一行，而不是单纯最新日期。
    val = execute_sql(
        """SELECT pe_ttm, pe, pb, market_cap, dividend_yield, dividend_yield_ttm,
                  turnover_rate_f, volume_ratio, total_share, float_share, free_share,
                  limit_status, ps_ratio AS ps_ttm
           FROM valuation_snapshots
           WHERE stock_id=?
           ORDER BY (volume_ratio IS NOT NULL) DESC, as_of_date DESC LIMIT 1""",
        (stock_id,)
    )
    if val and result["latest_indicators"]:
        result["latest_indicators"].update({k: v for k, v in val[0].items() if v is not None})
    elif val:
        result["latest_indicators"] = val[0]

    # 最新一期财报里的资产负债明细（eps/研发费用/货币资金/存货/商誉/固定资产）
    fin_detail = execute_sql(
        """SELECT eps, rd_exp, money_cap, inventories, goodwill, fix_assets
           FROM financial_reports WHERE stock_id=?
           ORDER BY period_end_date DESC LIMIT 1""",
        (stock_id,)
    )
    if fin_detail and result["latest_indicators"]:
        result["latest_indicators"].update({k: v for k, v in fin_detail[0].items() if v is not None})
    elif fin_detail:
        result["latest_indicators"] = fin_detail[0]

    # 最新一日成交量/成交额/换手率
    latest_quote = execute_sql(
        """SELECT trade_date, volume, amount, turnover FROM stock_daily_quotes
           WHERE stock_id=? AND close IS NOT NULL ORDER BY trade_date DESC LIMIT 1""",
        (stock_id,)
    )
    if latest_quote:
        q = latest_quote[0]
        result["volume"] = q["volume"]
        result["amount"] = q["amount"]
        result["turnover_rate"] = q["turnover"]

    # 获取行业
    industries = execute_sql("""
        SELECT it.name FROM stock_industries si
        JOIN industry_tags it ON si.industry_id=it.id
        WHERE si.stock_id=?""", (stock_id,))
    result["industry_list"] = [r["name"] for r in industries]

    # 概念标签（THS+DC 概念板块）
    concepts = execute_sql(
        """SELECT DISTINCT name FROM stock_concept_boards WHERE stock_id=?""",
        (stock_id,)
    )
    result["concepts"] = [r["name"] for r in concepts]

    # parse growth data from factor_scores JSON (CAGR 等仍用年报)
    if scores and scores[0].get("score_detail_json"):
        import json
        detail = json.loads(scores[0]["score_detail_json"])
        g = detail.get("growth", {})
        if g.get("revenue_cagr_3y") is not None:
            result["revenue_cagr_3y"] = round(g["revenue_cagr_3y"] * 100, 1)
        if g.get("profit_cagr_3y") is not None:
            result["profit_cagr_3y"] = round(g["profit_cagr_3y"] * 100, 1)

    # 顶部卡片：优先最新单季同比（与季度表口径一致）
    from services.data_processor import enrich_reports_with_yoy, select_quarterly_reports

    all_reports = execute_sql(
        """SELECT report_type, period_end_date,
                  COALESCE(revenue, operating_revenue, 0) as revenue,
                  COALESCE(net_profit_parent, net_profit, 0) as net_profit_parent
           FROM financial_reports WHERE stock_id=?
           ORDER BY period_end_date DESC LIMIT 16""",
        (stock_id,),
    )
    if all_reports:
        reports, quarterly, granularity = select_quarterly_reports(
            [dict(r) for r in all_reports], periods=8
        )
        enrich_reports_with_yoy(reports)
        latest = reports[-1] if reports else None
        if latest:
            result["growth_granularity"] = granularity
            result["revenue_yoy"] = latest.get("revenue_yoy")
            result["revenue_yoy_reliable"] = latest.get("revenue_yoy_reliable")
            result["revenue_yoy_note"] = latest.get("revenue_yoy_note")
            result["profit_yoy"] = latest.get("profit_yoy")
            result["profit_yoy_raw"] = latest.get("profit_yoy_raw")
            result["profit_yoy_reliable"] = latest.get("profit_yoy_reliable")
            result["profit_yoy_note"] = latest.get("profit_yoy_note")
            result["profit_yoy_change"] = latest.get("profit_yoy_change")
            result["profit_yoy_period"] = latest.get("period_end_date")

    return result


@router.put("/stocks/{stock_id}")
async def update_stock(stock_id: int, body: dict):
    """编辑股票信息（含多行业）"""
    existing = execute_sql("SELECT id FROM stocks WHERE id=?", (stock_id,))
    if not existing:
        raise HTTPException(status_code=404, detail="股票不存在")
    if "name" in body:
        execute_update("UPDATE stocks SET name=?, updated_at=datetime('now') WHERE id=?", (body["name"], stock_id))
    # 多行业更新
    if "industries" in body:
        execute_update("DELETE FROM stock_industries WHERE stock_id=?", (stock_id,))
        for name in body["industries"]:
            name = name.strip()
            if not name: continue
            execute_insert("INSERT OR IGNORE INTO industry_tags(name) VALUES(?)", (name,))
            tid_row = execute_sql("SELECT id FROM industry_tags WHERE name=?", (name,))
            if tid_row:
                execute_insert("INSERT OR IGNORE INTO stock_industries(stock_id,industry_id) VALUES(?,?)",
                              (stock_id, tid_row[0]["id"]))
    # 同时兼容旧版单字段 industry（向后兼容）
    if "industry" in body and "industries" not in body:
        val = body["industry"].strip()
        if val:
            execute_update("DELETE FROM stock_industries WHERE stock_id=?", (stock_id,))
            execute_insert("INSERT OR IGNORE INTO industry_tags(name) VALUES(?)", (val,))
            tid_row = execute_sql("SELECT id FROM industry_tags WHERE name=?", (val,))
            if tid_row:
                execute_insert("INSERT OR IGNORE INTO stock_industries(stock_id,industry_id) VALUES(?,?)",
                              (stock_id, tid_row[0]["id"]))
    return {"ok": True}


@router.delete("/stocks/{stock_id}")
async def delete_stock(stock_id: int):
    """软删除股票"""
    affected = execute_update(
        "UPDATE stocks SET is_active=0, updated_at=datetime('now') WHERE id=?",
        (stock_id,)
    )
    if affected == 0:
        raise HTTPException(status_code=404, detail="股票不存在")
    return {"ok": True}


@router.post("/stocks/{stock_id}/fetch")
async def fetch_stock_data(stock_id: int):
    """手动触发单只股票数据抓取（与 /api/data/fetch 共用任务系统）"""
    import asyncio
    from services import fetch_job

    stock = execute_sql("SELECT * FROM stocks WHERE id=? AND is_active=1", (stock_id,))
    if not stock:
        raise HTTPException(status_code=404, detail="股票不存在")
    fetch_job.reset_stale_jobs()
    if fetch_job.is_running(stock_id):
        return {"ok": True, "status": "already_running", "stock_id": stock_id}
    s = stock[0]
    fetch_job.start_job(stock_id)
    asyncio.create_task(
        fetch_job.run_single_fetch_async(stock_id, s["code"], s["market"] or "A")
    )
    return {"ok": True, "status": "started", "stock_id": stock_id}


@router.get("/stocks/{stock_id}/quarterly")
def get_quarterly_financials(stock_id: int, periods: int = 8):
    """季度财务趋势数据（优先季报，不足时回退近期非年报）"""
    from services.data_processor import enrich_reports_with_yoy, select_quarterly_reports

    all_reports = execute_sql(
        """SELECT report_type, period_end_date,
                  COALESCE(revenue, operating_revenue, 0) as revenue,
                  COALESCE(net_profit_parent, net_profit, 0) as net_profit_parent,
                  COALESCE(gross_profit, 0) as gross_profit,
                  COALESCE(operating_cf, 0) as operating_cf
           FROM financial_reports WHERE stock_id=?
           ORDER BY period_end_date DESC LIMIT ?""",
        (stock_id, max(periods + 8, 16)),
    )
    reports, quarterly, granularity = select_quarterly_reports(
        [dict(r) for r in all_reports], periods=periods
    )
    enrich_reports_with_yoy(reports)

    reports = reports[-periods:] if len(reports) > periods else reports

    return {
        "stock_id": stock_id,
        "quarters": reports,
        "data_granularity": granularity,
        "quarterly_count": len(quarterly),
        "yoy_note": "利润/营收同比为单季同比；变动超过10倍时因基数过小不显示具体百分比，可查看原始值",
    }


@router.get("/stocks/{stock_id}/margin")
def get_margin_trading(stock_id: int):
    """主力资金流（东财 push2his, 需绕过代理 — 当前代理环境不可用，返回空）"""
    stock = execute_sql("SELECT code FROM stocks WHERE id=?", (stock_id,))
    if not stock:
        return []
    try:
        from services.margin_fetcher import fetch_margin_data
        data = fetch_margin_data(stock[0]["code"])
        if data:
            return data
    except Exception:
        pass
    return []  # 代理拦截，静默返回空


@router.get("/stocks/{stock_id}/dividends")
def get_dividend_history(stock_id: int):
    """分红历史（从财务报告提取）"""
    reports = execute_sql(
        """SELECT period_end_date, eps, raw_data_json FROM financial_reports
           WHERE stock_id=? AND eps IS NOT NULL AND eps > 0
           ORDER BY period_end_date DESC LIMIT 10""",
        (stock_id,)
    )
    results = []
    for r in reports:
        date = r["period_end_date"][:10] if r["period_end_date"] else ""
        results.append({"date": date, "eps": r["eps"], "bonus_rmb": round(r["eps"] * 0.3, 2)})
    return results


@router.get("/stocks/{stock_id}/company")
def get_company_info(stock_id: int):
    """公司背景信息（Tushare stock_company）+ 现任管理层名单（stk_managers）。"""
    info = execute_sql(
        "SELECT * FROM stock_company_info WHERE stock_id=?", (stock_id,)
    )
    managers = execute_sql(
        """SELECT name, lev, title, gender, edu, birthday, begin_date, end_date
           FROM stock_managers WHERE stock_id=? AND (end_date='' OR end_date IS NULL)
           ORDER BY begin_date DESC""",
        (stock_id,)
    )
    return {
        "stock_id": stock_id,
        "info": dict(info[0]) if info else None,
        "managers": [dict(m) for m in managers],
    }


@router.get("/stocks/{stock_id}/earnings-alerts")
def get_earnings_alerts(stock_id: int, limit: int = 8):
    """业绩预告（forecast）+ 业绩快报（express）——公司自愿/条件披露，早于正式财报，
    但覆盖率明显低于正式财报，前端需标注"非正式数据"。"""
    forecast = execute_sql(
        """SELECT period_end_date, ann_date, type, p_change_min, p_change_max,
                  net_profit_min, net_profit_max, summary, change_reason
           FROM earnings_forecast WHERE stock_id=?
           ORDER BY period_end_date DESC LIMIT ?""",
        (stock_id, limit)
    )
    express = execute_sql(
        """SELECT period_end_date, ann_date, revenue, operate_profit, n_income,
                  total_assets, diluted_eps, diluted_roe, yoy_sales, yoy_dedu_np, perf_summary
           FROM earnings_express WHERE stock_id=?
           ORDER BY period_end_date DESC LIMIT ?""",
        (stock_id, limit)
    )
    return {
        "stock_id": stock_id,
        "forecast": [dict(r) for r in forecast],
        "express": [dict(r) for r in express],
    }


@router.get("/stocks/{stock_id}/alpha-factors")
def get_alpha_factors(stock_id: int):
    """Alpha 因子 v1：盈余惊喜 + 行业中性估值。均为独立信号，未并入 V5 综合分。"""
    from database import get as get_db_conn
    from services.alpha_factors_v1 import get_industry_neutral_valuation

    surprise = execute_sql(
        """SELECT period_end_date, actual_source, actual_growth, guided_growth,
                  guided_ann_date, actual_ann_date, surprise_pct, tier
           FROM earnings_surprise_factor WHERE stock_id=?
           ORDER BY period_end_date DESC LIMIT 4""",
        (stock_id,)
    )

    conn = get_db_conn()
    val = get_industry_neutral_valuation(conn, stock_id)

    return {
        "stock_id": stock_id,
        "earnings_surprise": [dict(r) for r in surprise],
        "industry_neutral_valuation": val,
    }


@router.get("/stocks/{stock_id}/lhb-period-stats")
def get_lhb_period_stats(stock_id: int):
    """龙虎榜多周期上榜统计（近1/3/6/12月，AKShare stock_lhb_stock_statistic_em）。"""
    rows = execute_sql(
        """SELECT period, last_lhb_date, close, change_pct, lhb_count, lhb_net_amount,
                  lhb_buy_amount, lhb_sell_amount, inst_buy_count, inst_sell_count,
                  inst_net_amount, chg_1m, chg_3m, chg_6m, chg_1y
           FROM stock_lhb_period_stats WHERE stock_id=?
           ORDER BY CASE period WHEN '1m' THEN 1 WHEN '3m' THEN 2 WHEN '6m' THEN 3 ELSE 4 END""",
        (stock_id,)
    )
    return {"stock_id": stock_id, "periods": [dict(r) for r in rows]}


@router.get("/stocks/{stock_id}/moneyflow-detail")
def get_moneyflow_detail(stock_id: int, days: int = 10):
    """个股资金流明细：L2 大小单口径（moneyflow）+ 东方财富口径（moneyflow_dc）。
    两者是独立数据源，数值有差异属正常，不是重复数据。"""
    l2 = execute_sql(
        """SELECT trade_date, buy_sm_amount, sell_sm_amount, buy_md_amount, sell_md_amount,
                  buy_lg_amount, sell_lg_amount, buy_elg_amount, sell_elg_amount, net_mf_amount
           FROM stock_moneyflow_l2_daily WHERE stock_id=?
           ORDER BY trade_date DESC LIMIT ?""",
        (stock_id, days)
    )
    dc = execute_sql(
        """SELECT trade_date, net_amount, net_amount_rate, buy_elg_amount, buy_lg_amount,
                  buy_md_amount, buy_sm_amount
           FROM stock_moneyflow_dc_daily WHERE stock_id=?
           ORDER BY trade_date DESC LIMIT ?""",
        (stock_id, days)
    )
    return {
        "stock_id": stock_id,
        "l2": [dict(r) for r in l2],
        "dc": [dict(r) for r in dc],
    }


@router.get("/stocks/valuation/{stock_id}")
async def stock_valuation(stock_id: int):
    """个股估值详情"""
    import sqlite3
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM valuation_scores WHERE stock_id=? ORDER BY date DESC LIMIT 1",
                      (stock_id,)).fetchone()
    conn.close()
    return {"stock_id": stock_id, "valuation": dict(row) if row else None}


@router.post("/stocks/valuation/compute")
async def compute_valuation():
    """计算全量估值评分（预取快照 → 分位评分 → 同步至最新 comprehensive 行）"""
    from services.valuation_engine import compute_valuation_scores
    from services.valuation_prefetch import prefetch_valuation_snapshots

    prefetch = prefetch_valuation_snapshots()
    result = compute_valuation_scores()
    return {"prefetch": prefetch, **result}


@router.post("/stocks/valuation/analyze-all")
async def analyze_all_valuation():
    """与资金面/情绪面一致的别名：全量估值面计算"""
    return await compute_valuation()


@router.get("/stocks/{stock_id}/kline")
async def stock_kline(stock_id: int, period: str = "weekly", days: int = KLINE_DISPLAY_DAYS):
    """周K/月K 从日线聚合；days 为交易日窗口，日/周/月统一截断"""
    import sqlite3
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    rows = conn.execute("""SELECT trade_date, open, high, low, close, volume FROM stock_daily_quotes
        WHERE stock_id=? AND close IS NOT NULL ORDER BY trade_date""", (stock_id,)).fetchall()
    conn.close()
    if not rows:
        return {"stock_id": stock_id, "kline": [], "technical": []}
    window = rows[-days:] if len(rows) > days else rows
    if period == "daily":
        kline = [{"date": r["trade_date"], "open": r["open"], "high": r["high"],
                   "low": r["low"], "close": r["close"], "volume": r["volume"]} for r in window]
        from services.kline_technical import compute_technical_from_bars
        return {
            "stock_id": stock_id,
            "period": period,
            "kline": kline,
            "technical": compute_technical_from_bars(kline),
        }
    from collections import OrderedDict
    groups = OrderedDict()
    for r in window:
        dt = r["trade_date"]
        if period == "weekly":
            from datetime import datetime; d = datetime.strptime(dt, "%Y-%m-%d")
            key = f"{d.year}-W{d.isocalendar()[1]:02d}"
        else:
            key = dt[:7]
        if key not in groups:
            groups[key] = {"open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"], "volume": r["volume"]}
        else:
            g = groups[key]; g["high"] = max(g["high"], r["high"] or 0)
            g["low"] = min(g["low"], r["low"] or float("inf"))
            g["close"] = r["close"]; g["volume"] += r["volume"] or 0
    for k, v in groups.items():
        if period == "weekly":
            yr=int(k[:4]); wk=int(k[6:])
            from datetime import datetime as _dt; d=_dt.strptime(f"{yr}-W{wk:02d}-1","%G-W%V-%w")
            v["date"]=d.strftime("%Y-%m-%d")
        else:
            v["date"]=k+"-01"
    kline = [{"period": k, **v} for k, v in groups.items()]
    from services.kline_technical import compute_technical_from_bars
    return {
        "stock_id": stock_id,
        "period": period,
        "kline": kline,
        "technical": compute_technical_from_bars(kline),
    }

@router.get("/data/fetch-logs-summary")
async def fetch_logs_summary():
    """数据抓取日志摘要"""
    return {"summary": {}, "total": 0, "recent": []}
