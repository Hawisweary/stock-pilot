"""
AI基本面研究员 - FastAPI 应用入口
启动服务、注册路由、CORS 配置、自动调度
"""
import os
# Python 3.14 兼容: 手动加载 .env
_env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ[_k.strip()] = _v.strip()

import sys
import os
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "services"))

import threading
import time as _time
from datetime import datetime

# 调度器状态（旧版循环已下线，实际调度见 services/scheduler.py + scheduler_state 表）
_scheduler_state = {
    "running": False, "last_fetch": "", "last_recalc": "",
    "next_fetch": "每日15:30(services/scheduler.py)", "next_recalc": "随每日流水线",
}
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import (
    API_VERSION, API_TITLE, API_DESCRIPTION,
    CORS_ORIGINS, HOST, PORT, LOG_LEVEL,
)
import database as db
from api import register_blueprints
from middleware import ApiKeyMiddleware, RequestLogMiddleware
from services.logger import configure_root_logging

configure_root_logging(LOG_LEVEL)


def scheduler_loop():
    """后台自动调度：交易时段每30分钟检查一次数据更新"""
    global _scheduler_state
    _scheduler_state["running"] = True
    print("[Scheduler] 自动调度已启动（首次60s后检查）")
    last_fetch_date = datetime.now().strftime("%Y-%m-%d")  # 启动当天不重复抓取
    last_recalc_week = datetime.now().strftime("%Y-%W")
    _time.sleep(60)  # 启动60秒后再检查，避免和手动抓取冲突

    while True:
        try:
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            week_key = now.strftime("%Y-%W")

            # 交易日 18:00 自动抓取（每天一次）
            if now.hour == 18 and last_fetch_date != today_str:
                print(f"[Scheduler] {now.strftime('%H:%M')} 触发每日数据抓取...")
                _trigger_data_fetch()
                _scheduler_state["last_fetch"] = now.strftime("%Y-%m-%d %H:%M")
                last_fetch_date = today_str

            # 周一 09:00 自动重算评分（每周一次）
            if now.weekday() == 0 and now.hour == 9 and last_recalc_week != week_key:
                print(f"[Scheduler] {now.strftime('%H:%M')} 触发每周评分重算...")
                _trigger_score_recalc()
                _scheduler_state["last_recalc"] = now.strftime("%Y-%m-%d %H:%M")
                last_recalc_week = week_key

        except Exception as e:
            print(f"[Scheduler] 调度异常: {e}")

        _time.sleep(1800)  # 每30分钟检查一次


def _trigger_data_fetch():
    """内部触发数据抓取 — 使用 fetch_job 与 API 一致"""
    try:
        conn = db.get()
        stocks = conn.execute("SELECT id, code, market FROM stocks WHERE is_active=1 ORDER BY id").fetchall()
        print(f"[Scheduler] 抓取 {len(stocks)} 只股票数据")
        from services.fetch_job import scheduler_fetch_all
        from services.factor_engine import FactorEngine

        stock_list = [dict(s) for s in stocks]
        batch = scheduler_fetch_all(stock_list, sleep_sec=1.5)
        failed = batch.get("failed", [])
        engine = FactorEngine(conn)
        ids = [s["id"] for s in stocks]
        engine.calculate_all(ids)
        if failed:
            print(f"[Scheduler] 抓取+评分完成 (失败 {len(failed)}: {', '.join(failed[:5])})")
        else:
            print(f"[Scheduler] 抓取+评分完成")

        # 综合评分计算 + gap 同步
        try:
            from services.comprehensive import calculate_all
            calculate_all(ids)
            print("[Scheduler] 综合评分完成")
            from services.batch_score_maintenance import sync_gaps_after_fetch

            gap_sync = sync_gaps_after_fetch()
            if not gap_sync.get("skipped"):
                print(f"[Scheduler] 维度 gap 同步: required={gap_sync.get('after_sync_rate_required')}")
        except Exception as e:
            print(f"[Scheduler] 综合评分/gap同步失败: {e}")

        # 同时抓取新闻（不阻塞主流程）
        try:
            from services.news_fetcher import fetch_news_for_stock
            news_total = 0
            for s in stocks:
                added = fetch_news_for_stock(s["id"], s["code"])
                news_total += added
                _time.sleep(0.5)
            print(f"[Scheduler] 新闻更新: +{news_total} 条")
        except Exception as e:
            print(f"[Scheduler] 新闻抓取失败: {e}")
    except Exception as e:
        print(f"[Scheduler] 数据抓取失败: {e}")


def _trigger_score_recalc():
    """内部触发评分重算 + 综合分同步"""
    try:
        conn = db.get()
        stocks = conn.execute("SELECT id FROM stocks WHERE is_active=1").fetchall()
        from services.factor_engine import FactorEngine
        from services.comprehensive import calculate_all

        engine = FactorEngine(conn)
        ids = [s["id"] for s in stocks]
        engine.calculate_all(ids)
        calculate_all(ids)
        print(f"[Scheduler] 评分重算完成 ({len(stocks)}只)")
    except Exception as e:
        print(f"[Scheduler] 评分重算失败: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    print(f"[App] 启动 AI Fundamental Researcher v{API_VERSION}")
    db.init()
    try:
        from services.fetch_job import reset_stale_jobs
        n = reset_stale_jobs()
        if n:
            print(f"[App] 已重置 {n} 个超时抓取任务")
    except Exception as e:
        print(f"[App] 抓取任务状态初始化跳过: {e}")
    try:
        from services.job_queue import cleanup_stale_batch_jobs

        n = cleanup_stale_batch_jobs()
        if n:
            print(f"[App] 已清理 {n} 个中断 batch-fill job")
    except Exception as e:
        print(f"[App] batch-fill job 清理跳过: {e}")
    try:
        from services.report_rag import rebuild_fts_index
        fts_n = rebuild_fts_index()
        if fts_n:
            print(f"[App] FTS 索引已同步 {fts_n} 条")
    except Exception as e:
        print(f"[App] FTS 索引同步跳过: {e}")
    # 启动每日自动任务调度（唯一调度入口：services/scheduler.py，
    # 状态持久化+错过补跑+重任务子进程化）
    from services.scheduler import start_scheduler
    start_scheduler(app)

    # 启动即补跑过期的待执行建仓/调仓:后端常上下线,9:35 那一下可能没活着,
    # 导致 pending 单永远卡住(建仓点了没反应)。后台线程延迟执行,避开启动期写竞争。
    def _catchup_pending():
        import time as _t
        from services.portfolio_svc import execute_pending_orders_at_open
        # 先睡 90s 让启动期 warm_cache 等批任务跑完释放 DB 写锁,再补跑;
        # 仍锁则少量退避重试(execute 内部已保证异常不泄漏连接)。
        _t.sleep(90)
        for attempt in range(4):
            try:
                r = execute_pending_orders_at_open()  # 有过期单则自动绕过守卫补跑
                if r.get("executed"):
                    print(f"[App] 启动补跑待办: 执行 {r['executed']} 单 (调仓 {r.get('rebalances', 0)})")
                return
            except Exception as e:
                if "locked" in str(e).lower() and attempt < 3:
                    _t.sleep(120)
                    continue
                print(f"[App] 启动补跑待办跳过: {e}")
                return
    threading.Thread(target=_catchup_pending, daemon=True).start()
    # 旧版 fetch-recalc 循环已下线：18:00 逐股抓取(eastmoney已封、每股1.5s约2.2小时)
    # 与周一重算均被 15:30 每日流水线覆盖
    _scheduler_state["note"] = "legacy loop disabled; see services/scheduler.py"
    # 预热 beta-health + IC tab 缓存(内部聚合/IC计算 20~49s 且CPU密集)——
    # 走独立子进程(自带GIL),绝不阻塞 API 进程事件循环
    try:
        import os as _os, sys as _sys, subprocess as _sp
        _warm = _os.path.join(_os.path.dirname(__file__), "scripts", "run_scheduled_job.py")
        _sp.Popen([_sys.executable, _warm, "warm_cache"],
                  stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
    except Exception as e:
        print(f"[App] 缓存预热子进程跳过: {e}")
    try:
        from services.score_health_monitor import start_monitor_daemon

        start_monitor_daemon(app, interval_sec=300)
    except Exception as e:
        print(f"[App] ScoreHealthMonitor 跳过: {e}")
    try:
        from services.score_gap_log import cleanup_old_logs

        cleaned = cleanup_old_logs()
        if cleaned.get("deleted_general") or cleaned.get("deleted_alerts"):
            print(f"[App] score_gap_log 清理: {cleaned}")
    except Exception as e:
        print(f"[App] score_gap_log 清理跳过: {e}")
    yield
    db.close()


app = FastAPI(
    title=API_TITLE,
    description=API_DESCRIPTION,
    version=API_VERSION,
    lifespan=lifespan,
)

# CORS 中间件
origins = [o.strip() for o in CORS_ORIGINS.split(",")]
import os as _os, logging as _logging
if "*" in origins and _os.getenv("AFR_ENV", "").lower() == "production":
    _logging.getLogger("afr.app").warning(
        "⚠️  CORS_ORIGINS='*' 在生产环境下不安全，请设置 AFR_CORS_ORIGINS=https://your-domain.com"
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(RequestLogMiddleware)
app.add_middleware(ApiKeyMiddleware)


# 全局兜底：后台批任务持有写锁时，任何接口的锁冲突统一降级为 503 + 友好提示，
# 不再把 sqlite3.OperationalError 抛成 500 Internal Server Error
@app.exception_handler(__import__("sqlite3").OperationalError)
async def _sqlite_locked_handler(request, exc):
    from fastapi.responses import JSONResponse

    if "locked" in str(exc).lower():
        return JSONResponse(
            status_code=503,
            content={"detail": "数据后台更新中(批任务持有数据库锁)，请稍后重试"},
        )
    _logging.getLogger("afr.app").error("SQLite错误 %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=500, content={"detail": f"数据库错误: {exc}"})


# 注册所有 API 路由
register_blueprints(app)


@app.get("/api/version")
async def version():
    """API 版本信息"""
    from migrations import CURRENT_SCHEMA_VERSION
    return {
        "version": API_VERSION,
        "title": API_TITLE,
        "schema_version": CURRENT_SCHEMA_VERSION,
        "features": [
            "5-factor",
            "industry-benchmark",
            "valuation-snapshots",
            "fundamental-momentum",
            "partial-fetch",
            "deep-peers",
            "pdf-rag",
        ],
    }


@app.get("/api/health")
async def health():
    """健康检查"""
    return {"status": "ok", "db_initialized": db.is_initialized()}


@app.get("/api/scheduler/status")
async def scheduler_status():
    """调度器状态"""
    global _scheduler_state
    return _scheduler_state


# ===== 启动入口 =====
if __name__ == "__main__":
    import uvicorn
    print(f"[App] 启动服务 http://{HOST}:{PORT}")
    print(f"[App] API 文档 http://{HOST}:{PORT}/docs")
    uvicorn.run("app:app", host=HOST, port=PORT, reload=False)
