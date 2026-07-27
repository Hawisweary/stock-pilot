"""
Dashboard API
- GET /api/dashboard/overview       概览数据
- GET /api/dashboard/top-stocks     排名Top N
"""
from fastapi import APIRouter, Query
from api_utils import execute_sql
from config import DB_PATH

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

# 静态新鲜度查询表 — 禁止用 f-string 拼标识符，列名/表名在此固化
# 格式: (label, sql, result_col_index_or_name, use_datetime)
_FRESHNESS_QUERIES: list[tuple[str, str, int, bool]] = [
    ("quote",      "SELECT trade_date FROM stock_daily_quotes  WHERE stock_id=? ORDER BY trade_date DESC LIMIT 1", 0, False),
    ("fundamental","SELECT calc_date   FROM factor_scores       WHERE stock_id=? ORDER BY calc_date   DESC LIMIT 1", 0, False),
    ("technical",  "SELECT created_at  FROM tech_analysis_cache WHERE stock_id=? ORDER BY created_at  DESC LIMIT 1", 0, True),
    ("news",       "SELECT pub_date    FROM stock_news           WHERE stock_id=? ORDER BY pub_date    DESC LIMIT 1", 0, True),
    ("capital",    "SELECT date        FROM capital_scores       WHERE stock_id=? ORDER BY date        DESC LIMIT 1", 0, False),
    ("policy",     "SELECT date        FROM policy_scores        WHERE stock_id=? ORDER BY date        DESC LIMIT 1", 0, False),
    ("sentiment",  "SELECT date        FROM sentiment_scores     WHERE stock_id=? ORDER BY date        DESC LIMIT 1", 0, False),
    ("valuation",  "SELECT date        FROM valuation_scores     WHERE stock_id=? ORDER BY date        DESC LIMIT 1", 0, False),
]


@router.get("/overview")
async def dashboard_overview():
    """Dashboard 概览数据"""
    # 股票数量
    stock_count = execute_sql("SELECT COUNT(*) as cnt FROM stocks WHERE is_active=1")
    total = stock_count[0]["cnt"] if stock_count else 0

    # 数据逾期数量（最近3天没有行情数据，按每只股票最新行情日判断）
    stale = execute_sql("""
        SELECT COUNT(*) as cnt
        FROM stocks s
        WHERE s.is_active = 1
        AND (
            (SELECT MAX(trade_date) FROM stock_daily_quotes WHERE stock_id = s.id) IS NULL
            OR (SELECT MAX(trade_date) FROM stock_daily_quotes WHERE stock_id = s.id) < date('now', '-3 days')
        )
    """)
    stale_count = stale[0]["cnt"] if stale else 0

    # 逾期股票列表
    stale_list = execute_sql("""
        SELECT s.id, s.code, s.name,
            (SELECT MAX(trade_date) FROM stock_daily_quotes WHERE stock_id = s.id) as last_date
        FROM stocks s
        WHERE s.is_active = 1
        AND (
            (SELECT MAX(trade_date) FROM stock_daily_quotes WHERE stock_id = s.id) IS NULL
            OR (SELECT MAX(trade_date) FROM stock_daily_quotes WHERE stock_id = s.id) < date('now', '-3 days')
        )
        ORDER BY s.code
    """)
    stale_stock_list = [dict(r) for r in stale_list]

    # 平均 V5 综合分
    avg = execute_sql("""
        SELECT AVG(cs.composite_v5) as avg_score
        FROM comprehensive_scores cs
        JOIN stocks s ON cs.stock_id = s.id
        WHERE s.is_active = 1 AND cs.composite_v5 IS NOT NULL
        AND cs.calc_date = (SELECT MAX(calc_date) FROM comprehensive_scores)
    """)
    avg_score = round(avg[0]["avg_score"] or 0, 1)

    # Top 3 (按 V5 综合分降序，使用 v_stock_scores 视图)
    top3 = execute_sql("""
        SELECT stock_id, code, name,
               score AS composite_v5, veto_status,
               quality_score, industry_score, market_env_score
        FROM v_stock_scores
        WHERE score IS NOT NULL
        ORDER BY score DESC
        LIMIT 3
    """)
    top3_with_rank = []
    for i, row in enumerate(top3):
        r = dict(row)
        r["rank"] = i + 1
        top3_with_rank.append(r)

    # 最后更新时间
    last_update_row = execute_sql("SELECT MAX(fetch_time) as last_time FROM data_fetch_log")
    last_update = last_update_row[0]["last_time"] if last_update_row and last_update_row[0]["last_time"] else ""

    return {
        "stock_count": total,
        "active_stocks": total - stale_count,
        "stale_stocks": stale_count,
        "stale_stock_list": stale_stock_list,
        "avg_composite_score": avg_score,
        "avg_composite_v5": avg_score,
        "top_3_stocks": top3_with_rank,
        "last_update": last_update,
        "data_quality": {
            "daily_quotes": execute_sql("SELECT COUNT(DISTINCT stock_id) as c FROM stock_daily_quotes")[0]["c"],
            "financial_cover": execute_sql("SELECT COUNT(DISTINCT stock_id) as c FROM financial_reports")[0]["c"],
            "scoring_cover": execute_sql("SELECT COUNT(DISTINCT stock_id) as c FROM comprehensive_scores WHERE composite_v5 IS NOT NULL")[0]["c"],
            "valuation_cover": execute_sql("SELECT COUNT(DISTINCT stock_id) as c FROM valuation_snapshots WHERE pe_ttm IS NOT NULL")[0]["c"],
            "last_sync": last_update,
        }
    }


@router.get("/ml-top")
async def ml_top_experimental(
    limit: int = Query(10),
    horizon: int | None = Query(None, description="5/20/60 日 horizon"),
):
    """ML Top N（实验，需 QLIB 开启或 predictions 已 seed）"""
    from config import ML_DEFAULT_HORIZON, ML_HORIZONS, QLIB_ENABLED
    from services.ml_gate import is_ml_predictions_approved
    from services.ml_predictions import get_latest_predictions, list_ml_horizons

    enabled = QLIB_ENABLED or is_ml_predictions_approved()
    h = horizon if horizon is not None else ML_DEFAULT_HORIZON
    preds = get_latest_predictions(limit=limit, horizon=h) if enabled else []
    return {
        "experimental": True,
        "enabled": enabled,
        "horizon": h,
        "horizons_available": list(ML_HORIZONS),
        "horizon_status": list_ml_horizons() if enabled else [],
        "predictions": preds,
        "note": "非 V5 综合分；独立 ml_pred 策略（多 horizon）",
    }


# 兼容旧版前端（Tauri WebKit 可能缓存旧 JS 请求 /api/ml/top）
legacy_ml_router = APIRouter(prefix="/api/ml", tags=["legacy"])


@legacy_ml_router.get("/top")
async def ml_top_legacy(
    limit: int = Query(10),
    horizon: int | None = Query(None),
):
    return await ml_top_experimental(limit=limit, horizon=horizon)


@router.get("/top-stocks")
async def top_stocks(limit: int = 5):
    """获取 Top N 股票"""
    rows = execute_sql("""
        SELECT stock_id, code, name,
               score AS composite_v5,
               quality_score, industry_score, market_env_score
        FROM v_stock_scores
        WHERE score IS NOT NULL
        ORDER BY score DESC
        LIMIT ?
    """, (limit,))

    result = []
    for i, row in enumerate(rows):
        r = dict(row)
        r["rank"] = i + 1
        result.append(r)
    return result


@router.get("/health")
async def data_health():
    """数据健康度检查 — 各维度数据新鲜度"""
    import sqlite3
    from datetime import date, timedelta
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    today = date.today().strftime("%Y-%m-%d")

    stocks = conn.execute("SELECT id, code, name FROM stocks WHERE is_active=1").fetchall()
    health = []
    for s in stocks:
        sid = s["id"]
        h = {"stock_id": sid, "code": s["code"], "name": s["name"], "status": "ok", "issues": []}

        for label, sql, col, use_datetime in _FRESHNESS_QUERIES:
            try:
                row = conn.execute(sql, (sid,)).fetchone()
                if row is None:
                    last_date = None
                elif use_datetime:
                    last_date = str(row[0]).split(" ")[0]
                else:
                    last_date = row[0]
            except Exception:
                import logging
                logging.getLogger(__name__).exception("freshness check failed label=%s sid=%s", label, sid)
                last_date = None

            h[label] = last_date
            if not last_date:
                h["issues"].append(f"{label}缺失")
                h["status"] = "red"
            elif last_date < (date.today() - timedelta(days=1)).strftime("%Y-%m-%d"):
                h["issues"].append(f"{label}过时({last_date})")
                if h["status"] == "ok":
                    h["status"] = "yellow"

        health.append(h)

    conn.close()
    summary = {
        "total": len(health),
        "ok": sum(1 for h in health if h["status"] == "ok"),
        "yellow": sum(1 for h in health if h["status"] == "yellow"),
        "red": sum(1 for h in health if h["status"] == "red"),
    }
    return {"summary": summary, "stocks": health}


@router.get("/score-sync-health")
async def score_sync_health(target_date: str | None = None):
    """Comprehensive 维度同步健康 — 同步率、告警、最近补算 job。"""
    from services.score_sync_health import get_score_sync_health

    return get_score_sync_health(target_date=target_date)


@router.get("/score-sync-trend")
async def score_sync_trend(days: int = Query(7, ge=1, le=90)):
    """必需维度 sync_rate 趋势（按 target_date 聚合）。"""
    from services.score_sync_health import get_score_sync_trend

    return get_score_sync_trend(days=days)


@router.get("/briefing")
async def daily_briefing():
    """每日早报：Top5 + 预警 + 概要"""
    import sqlite3, json
    from datetime import date
    from services.score_sql import per_stock_latest_join

    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    today = date.today().strftime("%Y-%m-%d")
    join_cs = per_stock_latest_join("cs")

    # Top5（每只股票最新分）
    top5 = conn.execute(f"""
        SELECT s.code, s.name, cs.composite_v5, cs.quality_score,
               cs.industry_score, cs.market_env_score, cs.veto_status
        FROM stocks s
        {join_cs}
        WHERE s.is_active=1 AND cs.composite_v5 IS NOT NULL
        ORDER BY cs.composite_v5 DESC LIMIT 5
    """).fetchall()

    # 预警：情绪面<30(亢奋) 或 >70(恐慌)
    alerts = conn.execute("""
        SELECT s.code, s.name, ss.composite_score
        FROM sentiment_scores ss
        JOIN stocks s ON ss.stock_id=s.id
        WHERE s.is_active=1 AND ss.date=? AND (ss.composite_score < 30 OR ss.composite_score > 70)
        ORDER BY ss.composite_score
    """, (today,)).fetchall()

    # 整体统计（每只股票最新分）
    stats = conn.execute(f"""
        SELECT COUNT(*) total, ROUND(AVG(cs.composite_v5),1) avg,
               ROUND(MAX(cs.composite_v5),1) max, ROUND(MIN(cs.composite_v5),1) min
        FROM stocks s
        {join_cs}
        WHERE s.is_active=1 AND cs.composite_v5 IS NOT NULL
    """).fetchone()

    # 政策快照
    try:
        macro = conn.execute(
            "SELECT macro FROM policy_snapshot ORDER BY date DESC LIMIT 1"
        ).fetchone()
    except Exception:
        import logging; logging.getLogger(__name__).exception("policy_snapshot query failed")
        macro = None

    conn.close()

    return {
        "date": today,
        "stats": {"total": stats["total"], "avg": stats["avg"], "max": stats["max"], "min": stats["min"]},
        "top5": [dict(r) for r in top5],
        "alerts": [dict(r) for r in alerts],
        "alert_count": len(alerts),
        "macro": macro["macro"] if macro else "",
        "summary": f"{stats['total']}只股票 V5 综合分均值{stats['avg']}分，最高{stats['max']}分，{len(alerts)}只触发预警"
    }


@router.get("/alerts")
async def stock_alerts():
    """自选提醒：所有触发条件的股票"""
    import sqlite3, json
    from datetime import date
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    today = date.today().strftime("%Y-%m-%d")

    alerts = []

    # 1. 情绪面极值
    mood = conn.execute("""
        SELECT s.code, s.name, '情绪' as type,
               CASE WHEN ss.composite_score < 30 THEN '🔥极度亢奋' ELSE '❄️极度恐慌' END as msg,
               ss.composite_score as value
        FROM sentiment_scores ss JOIN stocks s ON ss.stock_id=s.id
        WHERE s.is_active=1 AND ss.date=? AND (ss.composite_score<30 OR ss.composite_score>70)
    """, (today,)).fetchall()
    alerts.extend([dict(r) for r in mood])

    # 2. 估值极低 (PE<10 或 PB<1)
    pe_low = conn.execute("""
        SELECT s.code, s.name, '估值' as type, '📉PE极低' as msg,
               vs.composite_score as value
        FROM valuation_scores vs JOIN stocks s ON vs.stock_id=s.id
        WHERE s.is_active=1 AND vs.date=? AND vs.pe_score >= 85
    """, (today,)).fetchall()
    alerts.extend([dict(r) for r in pe_low])

    # 3. 技术面金叉 (score>80)
    gold = conn.execute("""
        SELECT s.code, s.name, '技术' as type, '📈技术面强势' as msg,
               tc.score as value
        FROM tech_analysis_cache tc JOIN stocks s ON tc.stock_id=s.id
        WHERE s.is_active=1 AND tc.score >= 80
        AND tc.created_at >= DATE('now','-1 days')
    """).fetchall()
    alerts.extend([dict(r) for r in gold])

    # 4. 评分大幅变化
    surge = conn.execute("""
        WITH now AS (
            SELECT stock_id, composite_v5,
                   ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY calc_date DESC) rn
            FROM comprehensive_scores
        ),
        prev AS (
            SELECT stock_id, composite_v5,
                   ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY calc_date DESC) rn
            FROM comprehensive_scores
        )
        SELECT s.code, s.name,
               CASE WHEN (n.composite_v5-p.composite_v5)>5 THEN '⬆' ELSE '⬇' END || '大幅变动' as type,
               'V5综合' || ROUND(n.composite_v5-p.composite_v5,1) as msg,
               n.composite_v5 as value
        FROM now n JOIN stocks s ON n.stock_id=s.id
        LEFT JOIN prev p ON p.stock_id=n.stock_id AND p.rn=2
        WHERE n.rn=1 AND ABS(n.composite_v5-COALESCE(p.composite_v5,n.composite_v5))>5
    """).fetchall()
    alerts.extend([dict(r) for r in surge])

    conn.close()
    return {"date": today, "count": len(alerts), "alerts": alerts}


@router.get("/sector-rotation")
async def sector_rotation():
    """行业轮动热力图 — 每行业平均综合分（每只股票最新分）"""
    import sqlite3
    from services.score_sql import per_stock_latest_join

    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    join_cs = per_stock_latest_join("cs")

    rows = conn.execute(f"""
        SELECT s.industry_sw as industry,
               ROUND(AVG(cs.composite_v5),1) as avg_score,
               COUNT(*) as stock_count,
               ROUND(MAX(cs.composite_v5),1) as max_score,
               ROUND(MIN(cs.composite_v5),1) as min_score
        FROM stocks s
        {join_cs}
        WHERE s.is_active=1 AND cs.composite_v5 IS NOT NULL AND s.industry_sw IS NOT NULL
        GROUP BY s.industry_sw
        ORDER BY avg_score DESC
    """).fetchall()
    conn.close()

    industries = [dict(r) for r in rows]
    overall_avg = sum(r["avg_score"] for r in industries) / len(industries) if industries else 0

    return {
        "industries": industries,
        "overall_avg": round(overall_avg, 1),
        "sector_count": len(industries),
    }
