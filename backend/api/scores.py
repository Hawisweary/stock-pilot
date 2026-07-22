from config import DB_PATH

"""
因子评分 API
- GET  /api/scores/ranking?limit=20      股票排名
- GET  /api/stocks/{id}/scores           历史评分
- POST /api/scores/recalculate           重新计算所有评分
"""
import asyncio
import json
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from api_utils import execute_sql, execute_insert, execute_update
from rate_limit import rate_limit_heavy, rate_limit_write

router = APIRouter(prefix="/api", tags=["scores"])


class FactorWeightsBody(BaseModel):
    quality: float = Field(0.30, ge=0, le=1)
    growth: float = Field(0.25, ge=0, le=1)
    value: float = Field(0.20, ge=0, le=1)
    momentum: float = Field(0.10, ge=0, le=1)
    risk: float = Field(0.15, ge=0, le=1)


class BatchFillBody(BaseModel):
    mode: str = "sync_only"
    target_date: str | None = None
    stock_ids: list[int] | None = None
    dimensions: list[str] | None = None
    skip_no_source: bool = True
    dry_run: bool = False


class SparklineBody(BaseModel):
    stock_ids: list[int] = Field(..., min_length=1, max_length=500)
    days: int = Field(30, ge=1, le=365)
    metric: str = Field("composite_v5", description="composite_v5 | composite_score (legacy)")


def _authoritative_score_sql(alias: str = "") -> str:
    """v3 权威分：优先 composite_v5，历史行 fallback composite_score。"""
    p = f"{alias}." if alias else ""
    return f"COALESCE({p}composite_v5, {p}composite_score)"


def _recalculate_sync(stock_ids: list[int], benchmark: str) -> list:
    from services.factor_engine import FactorEngine
    from services.comprehensive_store import sync_factor_fundamental
    from database import get

    engine = FactorEngine(get(), benchmark_mode=benchmark)
    results = engine.calculate_all(stock_ids)
    for r in results:
        if r.get("composite_score") is not None and "error" not in r:
            sync_factor_fundamental(r["stock_id"], float(r["composite_score"]), r.get("calc_date"))
    return results


@router.get("/scores/ranking")
async def score_ranking(
    limit: int = 20,
    dual: bool = Query(False, description="灰度双轨：同时返回五因子/八维分"),
    client_key: str = Query(None, description="灰度分流键"),
):
    """获取股票综合评分排名（每只股票取最新 calc_date）"""
    import sqlite3
    from services.score_sql import per_stock_latest_join
    from services.gray_release import in_gray_bucket, gray_status

    show_dual = dual or in_gray_bucket(client_key)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # v3.0: 使用 v_stock_scores 视图（= composite_v5 权威分）替换旧双 JOIN 排名
    rows = conn.execute(
        """
        SELECT stock_id, code, name, industry_sw,
               score,
               veto_status,
               fundamental_score as profitability_score,
               val_score as value_score,
               capital_score, policy_score, mood_score,
               technical_score, sentiment_score as news_score
        FROM v_stock_scores
        WHERE score IS NOT NULL
        ORDER BY score DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    result = []
    for i, row in enumerate(rows):
        r = dict(row)
        r["rank"] = i + 1
        result.append(r)
    out = {"rankings": result, "dual_mode": False}
    if show_dual:
        out["gray"] = gray_status(client_key)
    return result


@router.get("/stocks/{stock_id}/scores")
async def stock_scores(stock_id: int):
    """获取某只股票的历史因子评分"""
    rows = execute_sql(
        """SELECT * FROM factor_scores
           WHERE stock_id=?
           ORDER BY calc_date DESC
           LIMIT 50""",
        (stock_id,)
    )
    return rows


@router.post("/scores/recalculate")
async def recalculate_scores(
    request: Request,
    benchmark: str = Query("industry", description="industry=行业内百分位 | watchlist=自选股池"),
):
    """重新计算所有活跃股票的因子评分"""
    rate_limit_heavy(request)
    stocks = execute_sql("SELECT id FROM stocks WHERE is_active=1")
    if not stocks:
        return {"updated": 0, "status": "no_stocks"}

    stock_ids = [s["id"] for s in stocks]
    results = await asyncio.to_thread(_recalculate_sync, stock_ids, benchmark)

    return {
        "updated": len(results),
        "status": "done",
        "benchmark_mode": benchmark,
    }


@router.get("/scores/factor-weights")
async def get_factor_weights():
    """五因子权重配置"""
    rows = execute_sql("SELECT * FROM factor_weights WHERE id=1")
    if not rows:
        return {
            "quality": 0.30,
            "growth": 0.25,
            "value": 0.20,
            "momentum": 0.10,
            "risk": 0.15,
        }
    r = rows[0]
    return {
        "quality": r.get("weight_quality", 0.30),
        "growth": r.get("weight_growth", 0.25),
        "value": r.get("weight_value", 0.20),
        "momentum": r.get("weight_momentum", 0.10),
        "risk": r.get("weight_risk", 0.15),
    }


@router.put("/scores/factor-weights")
async def update_factor_weights(body: FactorWeightsBody):
    """更新五因子权重（总和须为 1）"""
    total = body.quality + body.growth + body.value + body.momentum + body.risk
    if abs(total - 1.0) > 0.02:
        raise HTTPException(status_code=400, detail=f"权重之和须为 1，当前为 {total:.3f}")

    existing = execute_sql("SELECT id FROM factor_weights WHERE id=1")
    if existing:
        execute_update(
            """
            UPDATE factor_weights SET
                weight_quality=?, weight_growth=?, weight_value=?,
                weight_momentum=?, weight_risk=?
            WHERE id=1
            """,
            (body.quality, body.growth, body.value, body.momentum, body.risk),
        )
    else:
        execute_insert(
            """
            INSERT INTO factor_weights (
                id, weight_quality, weight_growth, weight_value, weight_momentum, weight_risk
            ) VALUES (1, ?, ?, ?, ?, ?)
            """,
            (body.quality, body.growth, body.value, body.momentum, body.risk),
        )
    return {"ok": True, "weights": body.model_dump()}


# ── 综合评分（基本面 + 技术面 + 新闻面）──

@router.get("/scores/comprehensive")
async def get_comprehensive_scores():
    """综合评分排名（v3：v_stock_scores / composite_v5 权威分）"""
    import sqlite3

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT stock_id, code, name, calc_date, score,
            score AS composite_v5,
            fundamental_score, technical_score,
            sentiment_score AS news_score,
            capital_score, policy_score, mood_score, val_score,
            veto_status
        FROM v_stock_scores
        WHERE score IS NOT NULL
        ORDER BY score DESC
        """,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/scores/batch")
async def scores_batch(limit: int = Query(None, ge=1, le=500)):
    """Dashboard 聚合：V5 综合分 + 维度分，一次返回（v3：无 debate_scores）"""
    import sqlite3

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    sql = """
        SELECT stock_id, code, name, industry_sw, calc_date,
            score,
            score AS composite_v5,
            fundamental_score, technical_score,
            sentiment_score AS news_score,
            capital_score, policy_score, mood_score, val_score,
            veto_status
        FROM v_stock_scores
        WHERE score IS NOT NULL
        ORDER BY score DESC
    """
    params: list = []
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    comprehensive = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    calc_dates = [r.get("calc_date") for r in comprehensive if r.get("calc_date")]
    display_date = max(calc_dates) if calc_dates else None
    return {
        "calc_date": display_date,
        "comprehensive": comprehensive,
        "count": len(comprehensive),
    }


@router.post("/scores/comprehensive/calculate")
async def calculate_comprehensive():
    """触发全量综合评分计算（同步 factor/tech/news 到 comprehensive_scores）"""
    import sqlite3

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    stocks = conn.execute("SELECT id FROM stocks WHERE is_active=1").fetchall()
    conn.close()
    from services.comprehensive import calculate_all

    return calculate_all([s["id"] for s in stocks])


@router.get("/scores/gaps")
async def get_score_gaps(
    target_date: str | None = None,
    stock_ids: str | None = Query(None, description="逗号分隔 stock_id"),
    dimensions: str | None = Query(None, description="逗号分隔维度列名"),
):
    """扫描 comprehensive 维度缺口与 sync_rate。"""
    from services.score_gap_scanner import scan_gaps

    parsed_ids = [int(x.strip()) for x in stock_ids.split(",") if x.strip()] if stock_ids else None
    parsed_dims = [x.strip() for x in dimensions.split(",") if x.strip()] if dimensions else None
    return scan_gaps(target_date=target_date, stock_ids=parsed_ids, dimensions=parsed_dims)


@router.post("/scores/batch-fill")
async def batch_fill_scores(request: Request, body: BatchFillBody):
    """批量补算维度分 — sync_only / compute_and_sync / force_recompute。"""
    rate_limit_heavy(request)
    allowed = {"sync_only", "compute_and_sync", "force_recompute"}
    if body.mode not in allowed:
        raise HTTPException(status_code=400, detail=f"mode 必须是 {sorted(allowed)}")

    if body.dry_run:
        from services.batch_score_orchestrator import fill_gaps

        return await asyncio.to_thread(
            fill_gaps,
            mode=body.mode,
            dimensions=body.dimensions,
            stock_ids=body.stock_ids,
            target_date=body.target_date,
            skip_no_source=body.skip_no_source,
            dry_run=True,
        )

    from services.job_queue import can_enqueue_batch_fill, enqueue_batch_fill

    ok, reason, running_id = can_enqueue_batch_fill()
    if not ok:
        raise HTTPException(
            status_code=409,
            detail={
                "message": reason,
                "running_job_id": running_id,
                "poll_url": f"/api/system/jobs/{running_id}",
            },
        )

    payload = {
        "mode": body.mode,
        "dimensions": body.dimensions,
        "stock_ids": body.stock_ids,
        "target_date": body.target_date,
        "skip_no_source": body.skip_no_source,
    }
    job = enqueue_batch_fill(payload)

    from services.score_gap_scanner import scan_gaps

    gaps = await asyncio.to_thread(
        scan_gaps,
        target_date=body.target_date,
        stock_ids=body.stock_ids,
    )
    return {
        "job_id": job.id,
        "status": "queued",
        "mode": body.mode,
        "estimated_gaps": gaps.get("missing_total", 0),
        "poll_url": f"/api/system/jobs/{job.id}",
    }


@router.get("/scores/gap-history")
async def score_gap_history(limit: int = 50, target_date: str | None = None):
    from services.score_gap_log import query_gap_history, sync_rate_trend

    return {
        "history": query_gap_history(limit=limit, target_date=target_date),
        "trend_7d": sync_rate_trend(days=7),
    }


@router.get("/scores/health")
async def score_health(target_date: str | None = None):
    from services.score_health_monitor import check_sync_rate

    return check_sync_rate(target_date=target_date)


@router.get("/stocks/{stock_id}/comprehensive")
async def get_stock_comprehensive(stock_id: int):
    from services.comprehensive_store import load_display_scores

    return load_display_scores(stock_id)

@router.get("/scores/trend/{stock_id}")
async def score_trend(stock_id: int, days: int = 30):
    """单只股票 V5 评分变化追踪（权威分 = composite_v5）"""
    import sqlite3

    score_expr = _authoritative_score_sql()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"""
        SELECT calc_date,
               {score_expr} AS score,
               composite_v5,
               fundamental_score, technical_score,
               sentiment_score, capital_score, policy_score, mood_score, val_score
        FROM comprehensive_scores
        WHERE stock_id=? AND {score_expr} IS NOT NULL
        ORDER BY calc_date DESC LIMIT ?
        """,
        (stock_id, days),
    ).fetchall()
    conn.close()
    trend = [dict(r) for r in rows]
    trend.reverse()
    return {"stock_id": stock_id, "trend": trend, "period": f"{days}天", "metric": "composite_v5"}


@router.post("/scores/sparkline")
async def scores_sparkline(body: SparklineBody):
    """批量 sparkline 数据（U1-3 前置；一次请求多股，禁止 N+1 trend）"""
    import sqlite3

    if body.metric not in ("composite_v5", "composite_score"):
        raise HTTPException(status_code=400, detail="metric 必须是 composite_v5 或 composite_score")
    score_expr = (
        _authoritative_score_sql()
        if body.metric == "composite_v5"
        else "composite_score"
    )
    ids = list(dict.fromkeys(int(i) for i in body.stock_ids))
    ph = ",".join("?" * len(ids))
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"""
        WITH ranked AS (
            SELECT stock_id, calc_date, {score_expr} AS score,
                   ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY calc_date DESC) AS rn
            FROM comprehensive_scores
            WHERE stock_id IN ({ph}) AND {score_expr} IS NOT NULL
        )
        SELECT stock_id, calc_date, score
        FROM ranked
        WHERE rn <= ?
        ORDER BY stock_id, calc_date ASC
        """,
        (*ids, body.days),
    ).fetchall()
    conn.close()
    series: dict[str, list[dict]] = {str(i): [] for i in ids}
    for r in rows:
        key = str(int(r["stock_id"]))
        series.setdefault(key, []).append(
            {"date": r["calc_date"], "score": float(r["score"])}
        )
    return {"days": body.days, "metric": body.metric, "series": series}


@router.get("/scores/trend-overview")
async def trend_overview():
    """全局评分变化概览：每只股票最新 vs 7天前（v5 权威分）"""
    import sqlite3

    score_expr = _authoritative_score_sql()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"""
        WITH ranked AS (
            SELECT stock_id, {score_expr} AS score,
                   ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY calc_date DESC) rn
            FROM comprehensive_scores
            WHERE {score_expr} IS NOT NULL
        ),
        latest AS (
            SELECT stock_id, score AS now_score FROM ranked WHERE rn = 1
        ),
        prev AS (
            SELECT stock_id, score AS prev_score FROM ranked WHERE rn = 2
        )
        SELECT s.code, s.name, l.now_score, p.prev_score,
               ROUND(l.now_score - COALESCE(p.prev_score, l.now_score), 1) AS change
        FROM latest l
        JOIN stocks s ON l.stock_id = s.id
        LEFT JOIN prev p ON p.stock_id = l.stock_id
        WHERE s.is_active = 1
        ORDER BY change DESC
        """,
    ).fetchall()
    conn.close()
    return {"trends": [dict(r) for r in rows], "metric": "composite_v5"}


@router.get("/scores/correlation")
async def correlation_matrix():
    """34只股票综合评分相关性矩阵（批量 SQL）"""
    import math
    import sqlite3

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        WITH ranked AS (
            SELECT stock_id, composite_score,
                   ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY calc_date DESC) AS rn
            FROM comprehensive_scores
            WHERE composite_score IS NOT NULL
        )
        SELECT s.code, s.name, r.composite_score, r.rn
        FROM ranked r
        JOIN stocks s ON s.id = r.stock_id
        WHERE s.is_active = 1 AND r.rn <= 30
        ORDER BY s.code, r.rn
        """
    ).fetchall()
    conn.close()

    series: dict[str, dict] = {}
    for r in rows:
        code = r["code"]
        if code not in series:
            series[code] = {"name": r["name"], "scores": []}
        series[code]["scores"].append(float(r["composite_score"]))

    codes = [c for c, v in series.items() if len(v["scores"]) >= 5]
    n = len(codes)
    matrix = []
    for i in range(n):
        row = {"code": codes[i], "name": series[codes[i]]["name"], "corrs": {}}
        si = series[codes[i]]["scores"]
        mi = sum(si) / len(si)
        std_i = math.sqrt(sum((x - mi) ** 2 for x in si) / (len(si) - 1)) if len(si) > 1 else 0
        for j in range(n):
            if i == j:
                row["corrs"][codes[j]] = 1.0
                continue
            sj = series[codes[j]]["scores"]
            mj = sum(sj) / len(sj)
            std_j = math.sqrt(sum((x - mj) ** 2 for x in sj) / (len(sj) - 1)) if len(sj) > 1 else 0
            if std_i > 0 and std_j > 0:
                cov = sum((si[k] - mi) * (sj[k] - mj) for k in range(min(len(si), len(sj)))) / min(len(si), len(sj))
                row["corrs"][codes[j]] = round(cov / (std_i * std_j), 2)
            else:
                row["corrs"][codes[j]] = 0.0
        matrix.append(row)

    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            if abs(matrix[i]["corrs"][codes[j]]) > 0.7:
                pairs.append(
                    {
                        "p1": codes[i],
                        "p2": codes[j],
                        "corr": matrix[i]["corrs"][codes[j]],
                        "name1": matrix[i]["name"],
                        "name2": matrix[j]["name"],
                    }
                )
    pairs.sort(key=lambda x: -abs(x["corr"]))

    return {"matrix": matrix, "high_corr_pairs": pairs[:10], "stock_count": n}


@router.post("/scores/multicyc/{stock_id}")
async def compute_multicyc(stock_id: int):
    """计算多周期技术面"""
    stock = execute_sql("SELECT code FROM stocks WHERE id=? AND is_active=1", (stock_id,))
    if not stock: raise HTTPException(status_code=404)
    from services.multicyc import compute_multicyc_signal
    return compute_multicyc_signal(stock_id, stock[0]["code"])


@router.get("/scores/multicyc/{stock_id}")
async def get_multicyc(stock_id: int):
    """获取多周期技术面"""
    import sqlite3
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM multicyc_scores WHERE stock_id=? ORDER BY date DESC LIMIT 1",
        (stock_id,)).fetchone()
    conn.close()
    return {"stock_id": stock_id, "score": dict(row) if row else None}


@router.get("/scores/heatmap")
async def score_heatmap():
    """评分热力图矩阵（v3.0：使用 v_stock_scores，综合分 = composite_v5）"""
    import sqlite3

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT stock_id, code, name, industry_sw AS industry,
                  score AS composite_score,
                  fundamental_score, technical_score,
                  sentiment_score, capital_score, policy_score,
                  mood_score, val_score
           FROM v_stock_scores
           WHERE score IS NOT NULL
           ORDER BY score DESC""",
    ).fetchall()
    conn.close()
    dims = ["fundamental_score","technical_score","sentiment_score",
            "capital_score","policy_score","mood_score","val_score","composite_score"]
    labels = ["基本面","技术面","新闻面","资金面","政策面","情绪面","估值面","综合"]
    matrix = []
    for r in rows:
        row = {"stock_id": r["stock_id"], "code": r["code"], "name": r["name"], "industry": r["industry"]}
        for d, l in zip(dims, labels):
            row[l] = r[d]
        matrix.append(row)
    return {"dims": labels, "matrix": matrix}


@router.get("/scores/debate-scores")
async def debate_scores():
    """v3.0: 辩论链路已移除，返回 410。"""
    from fastapi import HTTPException
    raise HTTPException(
        status_code=410,
        detail={"error": "debate_removed", "message": "辩论链路已在 v3.0 移除，请使用 composite_v5 作为权威分。"},
    )


@router.get("/factors/list")
async def factor_list():
    """因子库列表"""
    import sqlite3
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    factors = [dict(r) for r in conn.execute("SELECT * FROM factor_registry ORDER BY factor_id").fetchall()]
    conn.close()
    return {"factors": factors}


@router.get("/factors/health")
def factors_health():
    """全部因子健康度(IC/IR判定 strong/weak/decayed),用于因子库标色+衰减告警。读缓存。"""
    from services.factor_analysis_cache import factor_health_all

    return factor_health_all()


@router.get("/factors/quality/status")
async def factor_quality_status():
    """S0 因子数据质量状态（生命周期/披露日历/宽表）"""
    from services.factor_s0_setup import run_factor_s0_setup
    from services.stock_lifecycle import lifecycle_stats
    import sqlite3

    stats = lifecycle_stats()
    conn = sqlite3.connect(DB_PATH)
    wide = conn.execute("SELECT COUNT(*) FROM factor_values_wide").fetchone()[0] if conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='factor_values_wide'"
    ).fetchone() else 0
    cal = conn.execute("SELECT COUNT(*) FROM financial_calendar").fetchone()[0] if conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='financial_calendar'"
    ).fetchone() else 0
    adj_missing = conn.execute(
        "SELECT COUNT(*) FROM stock_daily_quotes WHERE adj_close IS NULL AND close IS NOT NULL"
    ).fetchone()[0]
    conn.close()
    return {
        "lifecycle": stats,
        "financial_calendar_rows": cal,
        "factor_values_wide_rows": wide,
        "adj_close_missing": adj_missing,
        "survivorship_adjusted_ic": True,
        "note": "IC 引擎已启用 stock_lifecycle 截面过滤",
    }


@router.post("/factors/s0-setup")
async def factor_s0_setup_api(migrate_wide: bool = True):
    """运行 S0 初始化：adj_close、生命周期、披露日历、宽表迁移"""
    from services.factor_s0_setup import run_factor_s0_setup
    from services.beta_health import attach_meta

    return attach_meta(run_factor_s0_setup(migrate_wide=migrate_wide))


@router.get("/factors/{factor_id}/analysis")
async def factor_analysis(factor_id: str, forward_days: int = 20):
    """因子 IC + 分层 + 单调性/换手/显著性/多空曲线（cache.db按数据日期缓存+每日预热）"""
    from services.factor_analysis_cache import compute_and_cache

    return compute_and_cache(factor_id, forward_days)


@router.get("/factors/values")
async def factor_values(factor_id: str = None):
    """获取因子值"""
    import sqlite3
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    q = "SELECT fv.*, s.code, s.name FROM factor_values fv JOIN stocks s ON fv.stock_id=s.id WHERE fv.date=(SELECT MAX(date) FROM factor_values) "
    args = []
    if factor_id: q += "AND fv.factor_id=?"; args = [factor_id]
    q += " ORDER BY fv.value DESC LIMIT 50"
    rows = [dict(r) for r in conn.execute(q, args).fetchall()]
    conn.close()
    return {"factor_id": factor_id, "values": rows}


@router.post("/factors/compute")
async def compute_factors_endpoint(
    async_mode: bool = Query(False, description="入队异步执行"),
    mode: str = Query("full", description="full=全量+回填, incremental=增量"),
):
    """计算因子（全量或增量）"""
    from services.beta_health import attach_meta

    incremental = mode == "incremental"
    if async_mode:
        from services.job_queue import enqueue

        job = enqueue(
            "factor_compute",
            {"backfill": not incremental, "incremental": incremental},
        )
        return {"job_id": job.id, "status": job.status.value, "mode": mode}

    if incremental:
        from services.factor_incremental import compute_factors_incremental

        return attach_meta(compute_factors_incremental())

    from services.factor_factory import compute_factors

    return attach_meta(compute_factors())


@router.get("/factors/{factor_id}/decay")
async def factor_decay(factor_id: str, forward_days: int = 20):
    from services.ic_engine import analyze_factor_decay

    return analyze_factor_decay(factor_id, forward_days=forward_days)


class FactorMergeRequest(BaseModel):
    factor_ids: list[str]
    name: str
    method: str = "equal"
    save_combination: bool = False


class FactorCombinationCreate(BaseModel):
    name: str
    factor_ids: list[str]
    weight_method: str = "equal"
    weights: dict[str, float] | None = None
    materialize: bool = True


@router.get("/factors/combinations")
async def list_factor_combinations(limit: int = 50):
    from services.factor_combinations import list_combinations

    return {"combinations": list_combinations(limit=limit)}


@router.post("/factors/combinations")
async def create_factor_combination(body: FactorCombinationCreate):
    from services.factor_combinations import create_combination
    from services.beta_health import attach_meta

    return attach_meta(create_combination(
        body.name,
        body.factor_ids,
        weight_method=body.weight_method,
        weights=body.weights,
        materialize=body.materialize,
    ))


@router.get("/factors/combinations/{combo_id}")
async def get_factor_combination(combo_id: int):
    from services.factor_combinations import get_combination

    combo = get_combination(combo_id)
    if not combo:
        return {"error": "not_found"}
    return combo


@router.delete("/factors/combinations/{combo_id}")
async def delete_factor_combination(combo_id: int):
    from services.factor_combinations import delete_combination

    return delete_combination(combo_id)


@router.post("/factors/combinations/{combo_id}/materialize")
async def materialize_factor_combination(combo_id: int):
    from services.factor_combinations import materialize_combination
    from services.beta_health import attach_meta

    return attach_meta(materialize_combination(combo_id))


@router.post("/factors/merge")
async def factor_merge_api(req: FactorMergeRequest):
    from services.factor_merge import merge_factors_equal, merge_factors_ic_ir

    if req.method == "ic_ir":
        result = merge_factors_ic_ir(req.factor_ids, req.name)
    elif req.method == "rolling_optimal":
        from services.factor_merge import merge_factors_rolling_optimal

        result = merge_factors_rolling_optimal(req.factor_ids, req.name)
    else:
        result = merge_factors_equal(req.factor_ids, req.name)

    if req.save_combination and "error" not in result:
        from services.factor_combinations import create_combination

        saved = create_combination(
            req.name,
            req.factor_ids,
            weight_method=req.method if req.method != "rolling_optimal" else "rolling_optimal",
            materialize=True,
        )
        result["combination"] = saved
    return result


@router.get("/factors/correlation")
def factor_correlation():
    from services.beta_health import attach_meta
    from services.custom_factor import factor_correlation_matrix
    from services.factor_analysis_cache import cached_by_date

    return attach_meta(cached_by_date("factor:correlation", factor_correlation_matrix, allow_inprocess=False))


@router.get("/factors/custom")
async def list_custom_factors():
    from services.custom_factor import list_custom_factors

    return {"factors": list_custom_factors()}


@router.post("/factors/custom")
async def create_custom_factor_api(name: str = Query(...), formula: str = Query(...)):
    from services.custom_factor import create_custom_factor as create_cf

    return create_cf(name, formula)


class FactorNeutralizeRequest(BaseModel):
    factor_id: str
    output_factor_id: str | None = None
    output_name: str | None = None
    max_dates: int | None = 60


class FactorOrthogonalizeRequest(BaseModel):
    factor_ids: list[str]
    name_prefix: str = "ortho"
    max_dates: int | None = 60


@router.post("/factors/neutralize")
async def factor_neutralize_api(body: FactorNeutralizeRequest):
    from config import FACTOR_NEUTRALIZE_ENABLED
    from services.beta_health import attach_meta
    from services.factor_neutralize import neutralize_factor

    if not FACTOR_NEUTRALIZE_ENABLED:
        return {"error": "AFR_FACTOR_NEUTRALIZE=false"}
    return attach_meta(
        neutralize_factor(
            body.factor_id,
            output_factor_id=body.output_factor_id,
            output_name=body.output_name,
            max_dates=body.max_dates,
        )
    )


@router.post("/factors/orthogonalize")
async def factor_orthogonalize_api(body: FactorOrthogonalizeRequest):
    from services.beta_health import attach_meta
    from services.factor_orthogonal import orthogonalize_factors

    return attach_meta(
        orthogonalize_factors(
            body.factor_ids,
            name_prefix=body.name_prefix,
            max_dates=body.max_dates,
        )
    )


class FactorExpressionValidate(BaseModel):
    formula: str


class FactorExpressionCompute(BaseModel):
    name: str
    formula: str
    factor_id: str | None = None
    max_days: int | None = 60


class FactorGpRunRequest(BaseModel):
    population: int = 12
    generations: int = 8
    forward_days: int = 20
    top_k: int = 3
    async_mode: bool = False


@router.post("/factors/expressions/validate")
async def validate_factor_expression(body: FactorExpressionValidate):
    from config import FACTOR_EXPRESSION_ENABLED
    from services.factor_expression import validate_expression

    if not FACTOR_EXPRESSION_ENABLED:
        return {"error": "AFR_FACTOR_EXPRESSION_ENABLED=false"}
    return validate_expression(body.formula)


@router.get("/factors/expressions")
async def list_factor_expressions():
    from services.factor_expression import list_expressions

    return {"expressions": list_expressions()}


@router.post("/factors/expressions/compute")
async def compute_factor_expression_api(body: FactorExpressionCompute):
    from config import FACTOR_EXPRESSION_ENABLED
    from services.beta_health import attach_meta
    from services.factor_expression import compute_expression

    if not FACTOR_EXPRESSION_ENABLED:
        return {"error": "AFR_FACTOR_EXPRESSION_ENABLED=false"}
    return attach_meta(
        compute_expression(
            body.formula,
            body.name,
            factor_id=body.factor_id,
            max_days=body.max_days or 60,
        )
    )


@router.post("/factors/gp/run")
async def factor_gp_run_api(body: FactorGpRunRequest):
    from config import FACTOR_GP_ENABLED
    from services.beta_health import attach_meta

    if not FACTOR_GP_ENABLED:
        return {"error": "AFR_FACTOR_GP_ENABLED=false"}
    if body.async_mode:
        from services.job_queue import enqueue

        job = enqueue(
            "factor_gp",
            {
                "population": body.population,
                "generations": body.generations,
                "forward_days": body.forward_days,
                "top_k": body.top_k,
            },
        )
        return {"job_id": job.id, "status": job.status.value}

    from services.factor_gp import run_gp_search

    return attach_meta(
        run_gp_search(
            population=body.population,
            generations=body.generations,
            forward_days=body.forward_days,
            top_k=body.top_k,
        )
    )


@router.get("/factors/gp/runs")
async def factor_gp_runs(limit: int = 20):
    from services.factor_gp import list_gp_runs

    return {"runs": list_gp_runs(limit=limit)}


@router.get("/scores/industry-trend")
async def industry_score_trend(stock_id: int, days: int = Query(30, ge=7, le=90)):
    """
    P2-1: 返回与指定股票同行业的近 N 天平均综合分（用于趋势图基准线）。
    同行业 = 相同 industry_sw 字段。
    """
    import sqlite3

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        stock = conn.execute(
            "SELECT industry_sw FROM stocks WHERE id=? AND is_active=1", (stock_id,)
        ).fetchone()
        if not stock or not stock["industry_sw"]:
            return {"available": False, "trend": []}

        ind = stock["industry_sw"]
        rows = conn.execute(
            """
            SELECT cs.calc_date, AVG(COALESCE(cs.composite_v5, cs.composite_score)) AS avg_score,
                   COUNT(*) AS n
            FROM comprehensive_scores cs
            JOIN stocks s ON s.id = cs.stock_id
            WHERE s.industry_sw = ? AND s.is_active = 1
              AND cs.stock_id != ?
              AND COALESCE(cs.composite_v5, cs.composite_score) IS NOT NULL
            GROUP BY cs.calc_date
            ORDER BY cs.calc_date DESC LIMIT ?
            """,
            (ind, stock_id, days),
        ).fetchall()
    finally:
        conn.close()

    trend = [{"date": r["calc_date"], "avg_score": round(r["avg_score"], 2), "n": r["n"]} for r in rows]
    trend.reverse()
    return {"available": True, "industry": ind, "trend": trend}


@router.get("/scores/percentile-ranks")
async def percentile_ranks():
    """A-3：返回全池最新 composite_v5 的百分位排名。
    Response: { pool_size, calc_date, ranks: { "<stock_id>": { raw, percentile, code, name } } }
    percentile = 0~100，越高越好（前 X% 表示击败了 X% 的股票）。
    """
    import sqlite3

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        latest = conn.execute(
            "SELECT MAX(calc_date) FROM comprehensive_scores WHERE composite_v5 IS NOT NULL"
        ).fetchone()[0]
        if not latest:
            return {"pool_size": 0, "calc_date": None, "ranks": {}}

        rows = conn.execute(
            """SELECT cs.stock_id, cs.composite_v5, s.code, s.name
               FROM comprehensive_scores cs
               JOIN stocks s ON s.id = cs.stock_id
               WHERE cs.calc_date = ? AND cs.composite_v5 IS NOT NULL
               ORDER BY cs.composite_v5 ASC""",
            (latest,),
        ).fetchall()

        pool = len(rows)
        result: dict[str, dict] = {}
        for rank_idx, r in enumerate(rows):
            # percentile = 超过了百分之多少的股票（0~100，越高越好）
            pct = round(rank_idx / max(pool - 1, 1) * 100, 1)
            result[str(r["stock_id"])] = {
                "raw": round(r["composite_v5"], 2),
                "percentile": pct,
                "code": r["code"],
                "name": r["name"],
            }
        return {"pool_size": pool, "calc_date": latest, "ranks": result}
    finally:
        conn.close()


# ─── M1：Profile 衍生分 API（opt-in only）────────────────────────────────────

@router.get("/scores/profiles/ranking")
async def profile_ranking(
    profile: str = Query(..., description="momentum | dividend"),
    limit: int = Query(default=50, ge=1, le=500),
):
    """
    动量/红利画像排行榜（opt-in，缺省 profile 参数则返回 400）。
    数据来源：stock_score_profiles（与 comprehensive_scores 完全隔离）。
    """
    import sqlite3
    if profile not in ("momentum", "dividend"):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="profile 须为 momentum 或 dividend")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        latest = conn.execute(
            "SELECT MAX(calc_date) FROM stock_score_profiles WHERE profile=?", (profile,)
        ).fetchone()[0]
        if not latest:
            return {"profile": profile, "calc_date": None, "stocks": [], "note": "尚无 profile 数据，请先运行 compute_all_v5_scores"}

        rows = conn.execute(
            """SELECT sp.stock_id, sp.score, s.code, s.name,
                      cs.composite_v5, cs.veto_status
               FROM stock_score_profiles sp
               JOIN stocks s ON s.id = sp.stock_id
               LEFT JOIN (
                 SELECT stock_id, composite_v5, veto_status FROM comprehensive_scores
                 WHERE calc_date = (SELECT MAX(calc_date) FROM comprehensive_scores WHERE composite_v5 IS NOT NULL)
               ) cs ON cs.stock_id = sp.stock_id
               WHERE sp.calc_date = ? AND sp.profile = ?
                 AND (cs.veto_status IS NULL OR cs.veto_status != 'exclude')
               ORDER BY sp.score DESC LIMIT ?""",
            (latest, profile, limit),
        ).fetchall()

        return {
            "profile": profile,
            "calc_date": latest,
            "stocks": [
                {
                    "stock_id": r["stock_id"],
                    "code": r["code"],
                    "name": r["name"],
                    "profile_score": round(r["score"], 1),
                    "composite_v5": round(r["composite_v5"], 1) if r["composite_v5"] is not None else None,
                    "veto_status": r["veto_status"] or "ok",
                }
                for r in rows
            ],
        }
    finally:
        conn.close()


@router.get("/scores/profiles/{stock_id}")
async def stock_profile_scores(stock_id: int):
    """
    单股 profile 分（momentum + dividend）+ 与 composite_v5 对比。
    数据来源：stock_score_profiles（opt-in展示，不影响任何决策链）。
    """
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT profile, score FROM stock_score_profiles
               WHERE stock_id = ?
               AND calc_date = (SELECT MAX(calc_date) FROM stock_score_profiles WHERE stock_id = ?)""",
            (stock_id, stock_id),
        ).fetchall()
        v5_row = conn.execute(
            """SELECT composite_v5, calc_date FROM comprehensive_scores
               WHERE stock_id = ?
               ORDER BY calc_date DESC LIMIT 1""",
            (stock_id,),
        ).fetchone()
        profiles = {r["profile"]: round(r["score"], 1) for r in rows}
        return {
            "stock_id": stock_id,
            "composite_v5": round(v5_row["composite_v5"], 1) if v5_row else None,
            "calc_date": v5_row["calc_date"] if v5_row else None,
            "profiles": profiles,
            "delta": {
                p: round(profiles[p] - (v5_row["composite_v5"] or 0), 1)
                for p in profiles if v5_row
            },
        }
    finally:
        conn.close()
