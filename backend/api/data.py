"""
数据抓取 API
- POST /api/data/fetch/{stock_id}   抓取单股票（后台任务）
- GET  /api/data/fetch/{stock_id}/status
- POST /api/data/fetch-all           后台批量抓取
"""
import asyncio
import sqlite3
import time
import threading
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from api_utils import execute_sql
from services import fetch_job
from config import (
    DB_PATH,
    FETCH_PARALLEL,
    FETCH_DEFAULT_MODE,
    FETCH_ALL_AUTO_SCORE,
    FETCH_ALL_PARALLEL,
)

router = APIRouter(prefix="/api/data", tags=["data"])

_fetch_all_status = {
    "running": False,
    "progress": "0/0",
    "started_at": "",
    "finished": True,
    "total": 0,
    "processed": 0,
    "success": 0,
    "phase": "",
    "mode": FETCH_DEFAULT_MODE,
    "warning": "",
    "error": "",
}
_fetch_started_mono: float = 0.0
_fetch_last_progress_mono: float = 0.0
_fetch_last_processed: int = -1
_fetch_worker_active: bool = False
_FETCH_STALE_FLOOR_SEC = 15 * 60
_FETCH_STALE_CAP_SEC = 2 * 60 * 60
# 连续这么久 processed 不增加才视为僵死（正常 99 只需 40～60min，不按总时长误杀）
_FETCH_PROGRESS_IDLE_SEC = 25 * 60


def _fetch_stale_seconds(total: int, mode: str = "incremental") -> float:
    """按股票数估算批量抓取最大耗时（与前端轮询对齐）。"""
    n = max(int(total or 0), 1)
    parallel = max(1, min(FETCH_ALL_PARALLEL, FETCH_PARALLEL))
    # 实测增量约 45～90s/股（2 并行），公式偏保守避免前端过早报超时
    per_stock = 55 if mode == "incremental" else 85
    factor_buffer = 300
    estimated = (n / parallel) * per_stock + factor_buffer
    return min(_FETCH_STALE_CAP_SEC, max(_FETCH_STALE_FLOOR_SEC, estimated))


_FINANCIAL_ERROR_STEPS = frozenset(
    {"financials", "financials_quarterly", "financials_fast", "financials_annual"}
)


def _financial_step_ok(plan, result: dict) -> bool:
    """财报是否视为成功（熔断器用，避免 count=0 但无错误时误触发）。"""
    if not plan.fetch_financials:
        return True
    return not any(
        e.get("step") in _FINANCIAL_ERROR_STEPS for e in result.get("errors", [])
    )


def _market_hours_warning() -> str:
    """盘中软提示，不硬拦。"""
    now = datetime.now()
    if now.weekday() >= 5:
        return ""
    minutes = now.hour * 60 + now.minute
    if 9 * 60 + 30 <= minutes < 11 * 60 + 30:
        return "当前为交易时段，批量抓取可能遇数据源限流，建议收盘后执行。"
    if 13 * 60 <= minutes < 15 * 60:
        return "当前为交易时段，批量抓取可能遇数据源限流，建议收盘后执行。"
    return ""


def _mark_fetch_progress(processed: int) -> None:
    """有新股完成时刷新心跳，供僵死检测使用。"""
    global _fetch_last_progress_mono, _fetch_last_processed
    if processed > _fetch_last_processed:
        _fetch_last_processed = processed
        _fetch_last_progress_mono = time.monotonic()


def _reset_fetch_all_status(progress: str = "0/0"):
    global _fetch_all_status, _fetch_started_mono, _fetch_worker_active
    global _fetch_last_progress_mono, _fetch_last_processed
    _fetch_all_status["running"] = False
    _fetch_all_status["finished"] = True
    _fetch_all_status["progress"] = progress
    _fetch_started_mono = 0.0
    _fetch_last_progress_mono = 0.0
    _fetch_last_processed = -1
    _fetch_worker_active = False


def _finish_fetch_all(
    *,
    progress: str,
    processed: int,
    success: int,
    total: int,
    phase: str = "完成",
    error: str = "",
) -> None:
    """批量抓取结束：保留 processed/success 供前端判断，避免误报失败。"""
    global _fetch_all_status, _fetch_started_mono
    _fetch_all_status.update(
        {
            "running": False,
            "finished": True,
            "progress": progress,
            "processed": processed,
            "success": success,
            "total": total,
            "phase": phase,
            "error": error,
        }
    )
    _fetch_started_mono = 0.0


def _maybe_reset_stale_fetch():
    """仅当进度长时间不增加时才重置（避免 99 只正常长跑被 13min 墙钟误杀）。"""
    global _fetch_all_status
    if not (_fetch_all_status.get("running") or _fetch_worker_active):
        return
    if _fetch_last_progress_mono <= 0:
        return
    idle_sec = time.monotonic() - _fetch_last_progress_mono
    if idle_sec <= _FETCH_PROGRESS_IDLE_SEC:
        return
    progress = _fetch_all_status.get("progress", "0/0")
    print(f"[FetchAll] 进度 {idle_sec:.0f}s 未更新，标记僵死（卡在 {progress}）")
    _fetch_all_status["error"] = (
        f"抓取进度超过 {_FETCH_PROGRESS_IDLE_SEC // 60} 分钟未更新，卡在 {progress}。"
        "可 POST /api/data/fetch-reset 后重试。"
    )
    _fetch_all_status["running"] = False
    _fetch_all_status["finished"] = True


@router.post("/fetch/{stock_id}")
async def fetch_stock_data(stock_id: int):
    """抓取单只股票（立即返回，后台执行）"""
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


@router.get("/fetch/{stock_id}/status")
async def fetch_stock_status(stock_id: int):
    """查询单股抓取进度"""
    fetch_job.reset_stale_jobs()
    return fetch_job.status_payload(stock_id)


@router.post("/fetch-all")
async def fetch_all_stocks(mode: str = Query(FETCH_DEFAULT_MODE, pattern="^(incremental|full)$")):
    """后台批量抓取所有股票（incremental 日常 / full 深度全量）"""
    global _fetch_all_status, _fetch_started_mono

    _maybe_reset_stale_fetch()

    if _fetch_worker_active or _fetch_all_status["running"]:
        total = int(_fetch_all_status.get("total") or 0)
        return {
            "status": "already_running",
            "progress": _fetch_all_status["progress"],
            "count": total,
            "total": total,
            "phase": _fetch_all_status.get("phase", ""),
            "mode": _fetch_all_status.get("mode", mode),
        }

    stocks = execute_sql("SELECT id, code, name, market FROM stocks WHERE is_active=1")
    if not stocks:
        return {"status": "no_stocks", "count": 0, "message": "没有跟踪的股票"}

    warning = _market_hours_warning()
    total_n = len(stocks)
    _fetch_all_status = {
        "running": True,
        "progress": f"0/{total_n}",
        "started_at": datetime.now().strftime("%H:%M:%S"),
        "finished": False,
        "total": total_n,
        "processed": 0,
        "success": 0,
        "phase": "启动",
        "mode": mode,
        "warning": warning,
        "error": "",
    }
    _fetch_started_mono = time.monotonic()

    def do_fetch():
        global _fetch_all_status, _fetch_worker_active, _fetch_last_progress_mono, _fetch_last_processed
        _fetch_worker_active = True
        _fetch_last_progress_mono = time.monotonic()
        _fetch_last_processed = 0
        _fetch_all_status["phase"] = "初始化"
        completed = 0
        done_count = 0
        total = total_n
        total_stock_ids = [s["id"] for s in stocks]
        fetch_mode = mode
        fetch_job.reset_stale_jobs()
        try:
            from services.fetch_planner import build_plans, build_plan
            from services.fetch_circuit import FetchCircuitBreaker, financial_step_ok
            from services.fetch_step_status import record_step

            planner_conn = sqlite3.connect(DB_PATH)
            planner_conn.row_factory = sqlite3.Row
            circuit = FetchCircuitBreaker()
            plans = build_plans(planner_conn, stocks, fetch_mode)
            planner_conn.close()

            _fetch_all_status["phase"] = "预取行情"
            _fetch_all_status["progress"] = f"0/{total}"
            try:
                from services.data_sources import tencent_quote

                codes_batch = [s["code"] for s in stocks]
                quotes = tencent_quote(codes_batch)
                conn = sqlite3.connect(DB_PATH)
                for s in stocks:
                    q = quotes.get(s["code"], {})
                    if q.get("pe_ttm") is not None:
                        conn.execute(
                            "INSERT OR REPLACE INTO valuation_snapshots(stock_id,pe_ttm,pb,market_cap,dividend_yield,as_of_date) VALUES(?,?,?,?,?,date('now'))",
                            (
                                s["id"],
                                q.get("pe_ttm"),
                                q.get("pb"),
                                q.get("market_cap"),
                                q.get("dividend_yield"),
                            ),
                        )
                conn.commit()
                conn.close()
                print(f"[FetchAll] 批量行情预取完成: {len(quotes)}/{len(stocks)}")
            except Exception as e:
                print(f"[FetchAll] 批量行情预取失败: {e}")

            from concurrent.futures import ThreadPoolExecutor, as_completed

            _fetch_all_status["phase"] = "并行抓取"
            _fetch_all_status["progress"] = f"0/{total}"
            plans_lock = threading.Lock()

            def fetch_one(s):
                sid = s["id"]
                try:
                    with plans_lock:
                        if circuit.financials_tripped:
                            plan = build_plan(
                                sid,
                                fetch_mode,
                                circuit_skip_financials=True,
                            )
                        else:
                            plan = plans.get(sid) or build_plan(sid, fetch_mode)

                    fetch_job.start_job(sid)
                    result = fetch_job.sync_fetch_one(
                        sid,
                        s["code"],
                        s["market"],
                        finance_fast=plan.finance_fast,
                        plan=plan,
                    )
                    fin_attempted = plan.fetch_financials
                    fin_ok = financial_step_ok(plan, result)
                    if fin_attempted and not fin_ok:
                        for err in result.get("errors", []):
                            if err.get("step") in ("financials", "financials_quarterly"):
                                record_step(
                                    sid,
                                    "financials",
                                    "error",
                                    err.get("message", ""),
                                )
                                break
                    with plans_lock:
                        circuit.record_financial(attempted=fin_attempted, ok=fin_ok)

                    fetch_job.complete_job(
                        sid,
                        result,
                        auto_score=FETCH_ALL_AUTO_SCORE,
                        sync_gaps=False,
                    )
                    return (sid, result.get("status") != "error")
                except Exception as e:
                    fetch_job.fail_job(sid, str(e))
                    print(f"[FetchAll] {s['code']} 失败: {e}")
                    return (sid, False)

            pool_workers = max(1, min(FETCH_ALL_PARALLEL, FETCH_PARALLEL))
            with ThreadPoolExecutor(max_workers=pool_workers) as pool:
                futures = {pool.submit(fetch_one, s): s for s in stocks}
                for fut in as_completed(futures):
                    done_count += 1
                    sid, ok = fut.result()
                    if ok:
                        completed += 1
                    _fetch_all_status["progress"] = f"{done_count}/{total}"
                    _fetch_all_status["processed"] = done_count
                    _fetch_all_status["success"] = completed
                    _mark_fetch_progress(done_count)

            _fetch_all_status["phase"] = "因子评分"
            _fetch_all_status["progress"] = f"{done_count}/{total}"
            try:
                from services.factor_engine import FactorEngine as _FE

                engine_db = sqlite3.connect(DB_PATH)
                engine_db.row_factory = sqlite3.Row
                engine_db.execute("PRAGMA journal_mode=WAL")
                fe = _FE(engine_db)
                if fetch_mode == "incremental":
                    fe.calculate_incremental(total_stock_ids, sync_comprehensive=False)
                else:
                    fe.calculate_all(total_stock_ids, sync_comprehensive=False)
                engine_db.close()
            except Exception as e:
                print(f"[FetchAll] 批量因子评分失败: {e}")

            try:
                from services.batch_score_maintenance import sync_gaps_after_fetch

                sync_gaps_after_fetch()
            except Exception as e:
                print(f"[FetchAll] 批末 gap 同步跳过: {e}")

            _finish_fetch_all(
                progress=f"{done_count}/{total}",
                processed=done_count,
                success=completed,
                total=total,
                phase="完成",
            )
        except Exception as e:
            print(f"[FetchAll] 批量抓取异常: {e}")
            _finish_fetch_all(
                progress=f"{done_count}/{total}",
                processed=done_count,
                success=completed,
                total=total,
                phase="异常",
                error=str(e)[:300],
            )
        finally:
            _fetch_worker_active = False

    threading.Thread(target=do_fetch, daemon=True).start()

    msg = f"后台正在{'增量' if mode == 'incremental' else '全量'}抓取 {len(stocks)} 只股票"
    if warning:
        msg = f"{msg}（{warning}）"
    return {
        "status": "started",
        "count": len(stocks),
        "mode": mode,
        "warning": warning,
        "message": msg,
    }


@router.get("/fetch-status")
async def fetch_status():
    _maybe_reset_stale_fetch()
    total = int(_fetch_all_status.get("total") or 0)
    mode = _fetch_all_status.get("mode", FETCH_DEFAULT_MODE)
    running = bool(_fetch_all_status.get("running") or _fetch_worker_active)
    return {
        **_fetch_all_status,
        "running": running,
        "finished": not running,
        "worker_active": _fetch_worker_active,
        "stale_after_sec": int(_fetch_stale_seconds(total, mode)),
        "progress_idle_limit_sec": _FETCH_PROGRESS_IDLE_SEC,
    }


@router.post("/fetch-reset")
async def fetch_reset():
    """手动清除批量抓取 UI 状态（不中断已在跑的后台线程，但允许重新发起）。"""
    global _fetch_worker_active
    _reset_fetch_all_status()
    _fetch_worker_active = False
    fetch_job.reset_stale_jobs()
    return {"ok": True, "running": False, "worker_active": False}


@router.get("/status")
async def data_status():
    rows = execute_sql("""
        SELECT
            s.id as stock_id, s.code, s.name,
            (SELECT MAX(trade_date) FROM stock_daily_quotes WHERE stock_id=s.id) as last_quote_date,
            (SELECT MAX(period_end_date) FROM financial_reports WHERE stock_id=s.id) as last_report_date
        FROM stocks s
        WHERE s.is_active=1
        ORDER BY s.code
    """)
    return rows


@router.get("/logs")
async def fetch_logs(limit: int = 20, stock_id: int | None = None):
    if stock_id:
        return execute_sql(
            """
            SELECT * FROM data_fetch_log
            WHERE stock_id=?
            ORDER BY fetch_time DESC LIMIT ?
            """,
            (stock_id, limit),
        )
    return execute_sql(
        "SELECT * FROM data_fetch_log ORDER BY fetch_time DESC LIMIT ?",
        (limit,),
    )


@router.get("/fetch-step-status")
async def fetch_step_status(stock_id: int | None = None):
    """每股抓取步骤状态（跳过 / 熔断 / 待修复）— 供数据页叠加展示。"""
    from services.fetch_step_status import get_summary

    if stock_id is not None:
        summary = get_summary([stock_id])
    else:
        summary = get_summary()
    return {"summary": summary}


@router.get("/fetch-logs-summary")
async def fetch_logs_summary():
    """每只股票各数据类型最近一次抓取结果（供数据管理页展示）"""
    rows = execute_sql(
        """
        SELECT stock_id, data_type, status, records_count, error_message, fetch_time, source
        FROM data_fetch_log
        ORDER BY id DESC
        LIMIT 8000
        """
    )
    summary: dict[int, dict[str, dict]] = {}
    for r in rows:
        sid = r["stock_id"]
        if sid is None:
            continue
        bucket = summary.setdefault(sid, {})
        dt = r["data_type"] or "unknown"
        if dt not in bucket:
            bucket[dt] = {
                "data_type": dt,
                "status": r["status"],
                "records_count": r.get("records_count", 0),
                "error_message": r.get("error_message") or "",
                "source": r.get("source") or "",
                "fetch_time": r.get("fetch_time"),
            }
    return {"summary": summary}


@router.post("/backfill-industries")
async def backfill_industries():
    """补全空 industry_sw + 归一化英文名行业"""
    from services.industry_backfill import backfill_missing_industries, normalize_all_industry_sw

    missing = backfill_missing_industries()
    normalized = normalize_all_industry_sw()
    return {"missing": missing, "normalized": normalized}
