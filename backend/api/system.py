from config import DB_PATH
"""错误监控 + 系统 API"""
from fastapi import APIRouter, HTTPException, Request
import sqlite3, json, traceback
from datetime import datetime
from pydantic import BaseModel
from api_utils import execute_sql

router = APIRouter(prefix="/api/system", tags=["system"])

# 内存错误队列
ERRORS = []

def track_error(module: str, error: str, context: dict = None):
    ts = datetime.now().isoformat()
    entry = {"time": ts, "module": module, "error": str(error)[:300], "context": context}
    ERRORS.append(entry)
    if len(ERRORS) > 500:
        ERRORS.pop(0)
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""CREATE TABLE IF NOT EXISTS error_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT, module TEXT,
            error TEXT, context_json TEXT)""")
        conn.execute("INSERT INTO error_logs (time, module, error, context_json) VALUES (?,?,?,?)",
                     (ts, module, str(error)[:500], json.dumps(context or {}, ensure_ascii=False)))
        conn.commit(); conn.close()
    except Exception:
        pass  # 日志写入失败不应影响主流程


@router.get("/errors")
async def list_errors(limit: int = 50):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM error_logs ORDER BY time DESC LIMIT ?", (limit,)).fetchall()
        conn.close()
        return {"count": len(rows), "memory_count": len(ERRORS), "errors": [dict(r) for r in rows]}
    except Exception:
        return {"count": len(ERRORS), "memory_count": len(ERRORS), "errors": ERRORS[-limit:]}


@router.get("/beta-health")
def beta_health():
    """Beta 模块数据就绪检查（同步 def → FastAPI 走线程池，重聚合不阻塞事件循环）"""
    from services.beta_health import get_beta_health

    return get_beta_health()


@router.post("/fix-policy-industry")
async def fix_policy_industry():
    """补全行业 + 归一化 + 后台重算政策面"""
    import threading

    def _run():
        from services.industry_backfill import backfill_missing_industries, normalize_all_industry_sw
        from services.policy_scorer import compute_policy_score
        from services.comprehensive_store import upsert_dimension_score

        backfill_missing_industries()
        normalize_all_industry_sw()
        stocks = execute_sql("SELECT id, code FROM stocks WHERE is_active=1")
        for s in stocks:
            try:
                r = compute_policy_score(s["id"], s["code"])
                upsert_dimension_score(s["id"], "policy_score", float(r["composite_score"]))
            except Exception as e:
                track_error("fix-policy-industry", str(e), {"stock_id": s["id"]})

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started", "message": "行业补全与政策面重算已在后台启动"}


@router.get("/features")
async def feature_flags():
    """前端模块开关（与 backend config 一致）"""
    from config import (
        ENABLE_ADVANCED,
        ENABLE_BACKTEST,
        ENABLE_FACTOR_LAB,
        ENABLE_PORTFOLIO,
        ENABLE_PREMIUM,
        ENABLE_RAG,
        API_VERSION,
        BACKTEST_ENGINE,
        USE_POLARS,
        FACTOR_MERGE_ENABLED,
        QLIB_ENABLED,
        GRAY_RELEASE_PCT,
        DUAL_SCORE_UI,
        QLIB_PREDICTIONS_APPROVED,
        SCORING_MODE,
    )
    from schema_glossary import SCHEMA_GLOSSARY_VERSION
    from services.beta_health import get_rust_backtest_status

    rust_backtest = get_rust_backtest_status()
    return {
        "version": API_VERSION,
        "backtest": ENABLE_BACKTEST,
        "portfolio": ENABLE_PORTFOLIO,
        "factor_lab": ENABLE_FACTOR_LAB,
        "factor_ic": ENABLE_FACTOR_LAB or ENABLE_ADVANCED,
        "premium": ENABLE_PREMIUM,
        "rag": ENABLE_RAG,
        "advanced": ENABLE_ADVANCED,
        "quant": {
            "backtest_engine": BACKTEST_ENGINE,
            "use_polars": USE_POLARS,
            "factor_merge": FACTOR_MERGE_ENABLED,
            "qlib_enabled": QLIB_ENABLED,
            "rust_backtest": rust_backtest,
            "rust_approved": rust_backtest["approved"],
            "rust_backtest_available": rust_backtest["available"],
            "qlib_predictions_approved": QLIB_PREDICTIONS_APPROVED,
            "gray_release_pct": GRAY_RELEASE_PCT,
            "dual_score_ui": DUAL_SCORE_UI,
        },
        "scoring_mode": SCORING_MODE,
        "schema_glossary_version": SCHEMA_GLOSSARY_VERSION,
    }


@router.get("/gray-status")
async def gray_status_api(client_key: str = None):
    from services.gray_release import gray_status

    return gray_status(client_key)


@router.post("/backfill/interest-coverage")
async def backfill_interest_api(async_mode: bool = False):
    if async_mode:
        from services.job_queue import enqueue

        job = enqueue("interest_backfill", {})
        return {"job_id": job.id, "status": job.status.value}
    from services.financial_backfill import backfill_interest_coverage

    return backfill_interest_coverage()


@router.post("/backfill/factor-history")
async def backfill_factor_history_api(days: int = 90, async_mode: bool = True):
    from services.job_queue import enqueue

    job = enqueue("factor_expand", {"days": days})
    return {"job_id": job.id, "status": job.status.value, "days": days}


@router.get("/ic-review")
async def ic_review_api():
    from services.ic_stability import review_factor_ic

    return review_factor_ic()


@router.post("/factor-merge/preset")
async def factor_merge_preset_api(async_mode: bool = False, skip_ic_check: bool = False):
    if async_mode:
        from services.job_queue import enqueue

        job = enqueue("factor_merge", {"skip_ic_check": skip_ic_check})
        return {"job_id": job.id, "status": job.status.value}
    from services.factor_merge_preset import run_preset_merges

    return run_preset_merges(skip_ic_check=skip_ic_check)


@router.post("/backfill/score-history")
async def backfill_score_history_api(days: int = 90):
    from services.score_history_expand import expand_score_history

    return expand_score_history(days=days)


@router.post("/ml/sync-comprehensive")
async def ml_sync_comprehensive_api(ml_weight: float = 0.08):
    from services.ml_predictions import sync_ml_to_comprehensive

    return sync_ml_to_comprehensive(ml_weight=ml_weight)


@router.get("/jobs/{job_id}")
async def job_status(job_id: str):
    from services.job_queue import get_job

    job = get_job(job_id)
    if not job:
        return {"error": "job not found"}
    return {
        "id": job.id,
        "type": job.job_type,
        "status": job.status.value,
        "result": job.result,
        "error": job.error,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "heartbeat_at": job.heartbeat_at,
        "poll_url": f"/api/system/jobs/{job.id}",
    }


@router.delete("/jobs/{job_id}")
async def cancel_job_api(job_id: str):
    from services.job_queue import cancel_job, get_job

    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if not cancel_job(job_id):
        raise HTTPException(status_code=400, detail=f"无法取消 status={job.status.value}")
    return {"job_id": job_id, "status": "cancelled"}


@router.get("/jobs")
async def list_jobs(limit: int = 20):
    from services.job_queue import list_jobs as _list

    jobs = _list(limit=limit)
    return {
        "jobs": [
            {
                "id": j.id,
                "type": j.job_type,
                "status": j.status.value,
                "created_at": j.created_at,
                "finished_at": j.finished_at,
            }
            for j in jobs
        ]
    }


@router.get("/upgrade-metrics")
async def upgrade_metrics():
    """方案书 §4.1 — 行业覆盖率、利息保障缺失率、迁移进度"""
    from services.upgrade_monitor import get_upgrade_dashboard

    return get_upgrade_dashboard()


@router.get("/migration-progress")
async def migration_progress():
    from services.upgrade_monitor import get_migration_progress

    return get_migration_progress()


@router.get("/pilot/summary")
async def pilot_summary():
    import os
    import json
    from config import DATA_DIR

    manifest_path = os.path.join(DATA_DIR, "pilot_manifest.json")
    if not os.path.isfile(manifest_path):
        return {"enabled": False, "message": "未初始化试点，运行 scripts/pilot_setup.py"}
    with open(manifest_path, encoding="utf-8") as f:
        m = json.load(f)
    from services.upgrade_monitor import get_data_quality_metrics, get_migration_progress

    pilot_db = m.get("pilot_db")
    dq = get_data_quality_metrics(pilot_db) if pilot_db and os.path.isfile(pilot_db) else {}
    mig = get_migration_progress(pilot_db) if pilot_db and os.path.isfile(pilot_db) else {}
    return {"enabled": True, "manifest": m, "pilot_data_quality": dq, "pilot_migration": mig}


@router.get("/pilot/score-compare")
async def pilot_score_compare():
    from services.score_compare import run_compare

    return run_compare(pilot_only=True)


@router.get("/status")
async def system_status():
    """系统健康检查（v3.0：加入 V5 覆盖率、schema 版本、scoring_mode）"""
    import config
    from migrations import CURRENT_SCHEMA_VERSION
    from schema_glossary import SCHEMA_GLOSSARY_VERSION

    conn = sqlite3.connect(DB_PATH)
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    table_count = len(tables)
    row_counts = {}
    for t in tables[:8]:
        tname = t[0].replace('"', '')  # 防止表名含引号
        cnt = conn.execute(f'SELECT COUNT(*) FROM "{tname}"').fetchone()[0]
        row_counts[tname] = cnt

    # V5 覆盖率指标
    total_active = conn.execute("SELECT COUNT(*) FROM stocks WHERE is_active=1").fetchone()[0]
    v5_scored = conn.execute(
        "SELECT COUNT(DISTINCT stock_id) FROM comprehensive_scores WHERE composite_v5 IS NOT NULL"
    ).fetchone()[0]
    v5_coverage_pct = round(v5_scored / total_active * 100, 1) if total_active else 0.0

    # 最新 V5 计算日期
    v5_latest_date = conn.execute(
        "SELECT MAX(calc_date) FROM comprehensive_scores WHERE composite_v5 IS NOT NULL"
    ).fetchone()[0]

    # 过去24h错误
    from datetime import datetime, timedelta
    cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
    errors_24h = sum(1 for e in ERRORS if e.get("time", "") >= cutoff)

    conn.close()
    from services.beta_health import get_rust_backtest_status

    return {
        "status": "running",
        "schema_version": CURRENT_SCHEMA_VERSION,
        "glossary_version": SCHEMA_GLOSSARY_VERSION,
        "scoring_mode": config.SCORING_MODE,
        "v5_coverage": {
            "active_stocks": total_active,
            "v5_scored": v5_scored,
            "coverage_pct": v5_coverage_pct,
            "latest_calc_date": v5_latest_date,
        },
        "tables": table_count,
        "rows": row_counts,
        "errors_24h": errors_24h,
        "uptime": "ok",
        "rust_backtest": get_rust_backtest_status(),
    }
