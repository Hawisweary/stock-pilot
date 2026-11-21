"""V5 数据源一键同步 — Phase 1+2 + Phase 3 W1/W2（东财/腾讯/巨潮/自算）。"""
from __future__ import annotations

import sqlite3
import time
from typing import Any, Callable

import config
from services.announcement_fetch import sync_all_announcements
from services.eastmoney_forecast_sync import (
    sync_industry_eps_revision,
    sync_stock_eps_forecast,
)
from services.event_classifier import get_stock_events, sync_event_classification
from services.fund_flow_sync import sync_stock_fund_flow
from services.industry_l2_sync import sync_industry_l2
from services.macro_sync import sync_macro_indicators
from services.mood_scorer import compute_all_mood_v5
from services.news_fetcher import sync_all_news
from services.policy_event_sync import sync_policy_v5
from services.data_quality import detect_and_write
from services.ml_impute import impute_v5_tables
from services.volatility_forecast import sync_forecast
from services.quality_metrics_calc import compute_all_v5_metrics
from services.risk_scanner import scan_risk_flags
from services.sector_fund_flow_sync import sync_sector_fund_flow
from services.tushare_event_sync import sync_tushare_event_data
from services.v5_scorer import compute_all_v5_scores
from services.job_log import noop_log

# 预设同步模式（API mode= / 调度器）
V5_SYNC_MODE_PRESETS: dict[str, dict[str, Any]] = {
    "daily": {
        "skip_macro": True,
        "skip_fund_flow": True,
        "skip_announcements": True,
        "skip_news_fetch": True,
        "skip_sector": True,
        "skip_industry_l2": True,
        "reclassify_events": False,
        "use_llm_events": True,
        "llm_only_missing_news": True,
        "skip_tushare_events": True,
        "regime_refresh_matrix": False,
    },
    "nightly": {
        "skip_macro": True,
        "skip_fund_flow": True,
        "skip_announcements": False,
        "skip_news_fetch": False,
        "skip_sector": True,
        "skip_industry_l2": True,
        "skip_metrics": True,
        "skip_eps_revision": True,
        "skip_risk": False,
        "skip_policy": False,
        "skip_mood": True,
        "skip_v5_scores": True,
        "skip_tushare_events": True,
        "reclassify_events": True,
        # 显式关闭 LLM(P14,2026-11-20 决定先不跑)。原来写的是 True,但全市场时
        # _resolve_llm_stock_ids 返回 None,随后 bool(None) 为 False,LLM 实际从未执行过。
        "use_llm_events": False,
        "llm_only_missing_news": False,
        # 公告/新闻:小并发 + 时间预算 + 游标续抓(见 services.rotating_fetch)
        "fetch_workers": config.NIGHTLY_FETCH_WORKERS,
        "announcement_budget_s": config.NIGHTLY_ANNOUNCEMENT_BUDGET_MIN * 60,
        "news_budget_s": config.NIGHTLY_NEWS_BUDGET_MIN * 60,
        "fetch_cursor": True,
    },
    "weekly": {
        "skip_macro": False,
        "skip_fund_flow": False,
        "skip_announcements": True,
        "skip_news_fetch": True,
        "skip_sector": True,
        "skip_industry_l2": True,
        "skip_metrics": False,
        "skip_eps_revision": False,
        "skip_risk": False,
        "skip_policy": False,
        "skip_mood": False,
        "skip_v5_scores": False,
        "skip_events": True,
        "reclassify_events": False,
        "use_llm_events": False,
        "skip_tushare_events": False,
        "tushare_event_days": 30,
        "skip_per_stock": False,
        "regime_refresh_matrix": True,
    },
}


def _summarize(r: Any) -> str:
    """步骤返回值的简短摘要(写日志用)。"""
    if not isinstance(r, dict):
        return str(r)[:120]
    keys = ("stocks", "processed", "added", "complete_cycle", "stopped_by_budget",
            "error_count", "count", "rows", "written", "updated", "ok")
    parts = [f"{k}={r[k]}" for k in keys if k in r]
    return " ".join(parts)[:200] if parts else f"keys={list(r)[:8]}"


class _StepRunner:
    """逐步执行并记录:开始 / 结束 / 耗时 / 摘要;异常记为 {"error": ...},不中断后续步骤。"""

    def __init__(self, result: dict, log: Callable[[str], None]):
        self.result = result
        self.log = log

    def __call__(self, name: str, fn: Callable[[], Any]) -> None:
        self.log(f"▶ {name} 开始")
        t0 = time.time()
        try:
            out = fn()
            self.result["steps"][name] = out
            self.log(f"✓ {name} {time.time() - t0:.0f}s {_summarize(out)}")
        except Exception as e:  # noqa: BLE001 —— 保持原有"单步失败不阻塞"语义
            self.result["steps"][name] = {"error": str(e)}
            self.log(f"✗ {name} {time.time() - t0:.0f}s 失败: {str(e)[:200]}")


def _active_stock_ids(stock_ids: list[int] | None = None) -> list[int]:
    conn = sqlite3.connect(config.DB_PATH)
    try:
        if stock_ids:
            ph = ",".join("?" * len(stock_ids))
            rows = conn.execute(
                f"SELECT id FROM stocks WHERE id IN ({ph}) AND is_active=1",
                stock_ids,
            ).fetchall()
        else:
            rows = conn.execute("SELECT id FROM stocks WHERE is_active=1").fetchall()
        return [int(r[0]) for r in rows]
    finally:
        conn.close()


def stocks_missing_news_events(stock_ids: list[int] | None = None) -> list[int]:
    """无已分类新闻面事件的股票 ID。"""
    missing: list[int] = []
    for sid in _active_stock_ids(stock_ids):
        if not get_stock_events(sid, limit=1):
            missing.append(sid)
    return missing


def _news_event_coverage(stock_ids: list[int] | None = None) -> dict:
    ids = _active_stock_ids(stock_ids)
    with_events = sum(1 for sid in ids if get_stock_events(sid, limit=1))
    return {
        "total_stocks": len(ids),
        "with_news_events": with_events,
        "missing_news_events": len(ids) - with_events,
        "coverage_pct": round(with_events / max(len(ids), 1) * 100, 1),
    }


def _resolve_llm_stock_ids(
    stock_ids: list[int] | None,
    *,
    use_llm_events: bool,
    llm_only_missing_news: bool,
) -> list[int] | None:
    """None=全市场 LLM；[] = 跳过 LLM；具体列表=仅这些股票。"""
    if not use_llm_events:
        return []
    if llm_only_missing_news:
        return stocks_missing_news_events(stock_ids)
    return stock_ids


def sync_v5_data_sources(
    *,
    stock_ids: list[int] | None = None,
    mode: str | None = None,
    skip_macro: bool = False,
    skip_fund_flow: bool = False,
    skip_sector: bool = False,
    skip_metrics: bool = False,
    skip_industry_l2: bool = False,
    skip_eps_revision: bool = False,
    skip_announcements: bool = False,
    skip_news_fetch: bool = False,
    skip_events: bool = False,
    reclassify_events: bool = True,
    use_llm_events: bool = True,
    llm_only_missing_news: bool = False,
    llm_event_limit_per_stock: int = 12,
    skip_risk: bool = False,
    skip_policy: bool = False,
    skip_mood: bool = False,
    skip_v5_scores: bool = False,
    skip_tushare_events: bool = False,
    tushare_event_days: int = 10,
    skip_per_stock: bool = True,
    announcement_limit: int = 30,
    news_limit: int = 15,
    skip_volatility_forecast: bool = False,
    log=None,
) -> dict:
    """同步 V5 评分数据源（含公告/新闻抓取、事件分类、EPS/风险/政策/情绪）。

    log:可选的分步日志器(services.job_log.JobLog),每步记录开始、耗时和结果摘要。
    """
    preset = V5_SYNC_MODE_PRESETS.get(mode or "", {})
    locals_snapshot = {
        "stock_ids": stock_ids,
        "skip_macro": skip_macro,
        "skip_fund_flow": skip_fund_flow,
        "skip_sector": skip_sector,
        "skip_metrics": skip_metrics,
        "skip_industry_l2": skip_industry_l2,
        "skip_eps_revision": skip_eps_revision,
        "skip_announcements": skip_announcements,
        "skip_news_fetch": skip_news_fetch,
        "skip_events": skip_events,
        "reclassify_events": reclassify_events,
        "use_llm_events": use_llm_events,
        "llm_only_missing_news": llm_only_missing_news,
        "llm_event_limit_per_stock": llm_event_limit_per_stock,
        "skip_risk": skip_risk,
        "skip_policy": skip_policy,
        "skip_mood": skip_mood,
        "skip_v5_scores": skip_v5_scores,
        "skip_tushare_events": skip_tushare_events,
        "tushare_event_days": tushare_event_days,
        "skip_per_stock": skip_per_stock,
        "announcement_limit": announcement_limit,
        "news_limit": news_limit,
        "skip_volatility_forecast": skip_volatility_forecast,
    }
    opts = {**locals_snapshot, **preset}
    if stock_ids is not None:
        opts["stock_ids"] = stock_ids

    result: dict = {"steps": {}, "mode": mode or "custom"}
    log = log or noop_log
    step = _StepRunner(result, log)
    sids = opts["stock_ids"]
    workers = int(opts.get("fetch_workers") or 1)
    use_cursor = bool(opts.get("fetch_cursor")) and sids is None

    def _with_conn(fn):
        def _run():
            with sqlite3.connect(config.DB_PATH, timeout=120) as conn:
                conn.execute("PRAGMA busy_timeout=30000")
                return fn(conn)
        return _run

    def _regime(conn):
        from services.regime_pipeline import run_regime_l2_l3_pipeline

        if mode == "weekly":
            refresh_matrix = opts.get(
                "regime_refresh_matrix", config.REGIME_PIPELINE_WEEKLY_REFRESH_MATRIX,
            )
        else:
            refresh_matrix = opts.get(
                "regime_refresh_matrix", config.REGIME_PIPELINE_DAILY_REFRESH_MATRIX,
            )
        return run_regime_l2_l3_pipeline(conn, refresh_matrix=bool(refresh_matrix))

    def _eps():
        eps = sync_stock_eps_forecast(sids)
        eps["industry"] = sync_industry_eps_revision(trade_date=eps.get("as_of_date"))
        return eps

    def _events():
        llm_targets = _resolve_llm_stock_ids(
            sids,
            use_llm_events=opts["use_llm_events"],
            llm_only_missing_news=opts["llm_only_missing_news"],
        )
        use_llm = bool(opts["use_llm_events"] and llm_targets)
        r = sync_event_classification(
            sids,
            reclassify=opts["reclassify_events"],
            use_llm=use_llm,
            llm_limit_per_stock=opts["llm_event_limit_per_stock"],
            llm_stock_ids=llm_targets if use_llm else [],
        )
        if opts["llm_only_missing_news"]:
            r["llm_missing_only"] = llm_targets
        return r

    if not opts["skip_announcements"]:
        step("announcements", lambda: sync_all_announcements(
            sids,
            limit=opts["announcement_limit"],
            workers=workers,
            budget_s=opts.get("announcement_budget_s"),
            cursor_key="announcements" if use_cursor else None,
            log=log,
        ))
    if not opts["skip_news_fetch"]:
        step("news_fetch", lambda: sync_all_news(
            sids,
            limit=opts["news_limit"],
            workers=workers,
            budget_s=opts.get("news_budget_s"),
            cursor_key="news" if use_cursor else None,
            log=log,
        ))
    if not opts["skip_macro"]:
        step("macro", sync_macro_indicators)
    step("regime_pipeline", _with_conn(_regime))
    if not opts["skip_sector"]:
        step("sector_fund_flow", sync_sector_fund_flow)
    if not opts["skip_fund_flow"]:
        step("stock_fund_flow", lambda: sync_stock_fund_flow(sids))
    if not opts["skip_industry_l2"]:
        step("industry_l2", lambda: sync_industry_l2(sids))
    if not opts["skip_metrics"]:
        step("v5_metrics", lambda: compute_all_v5_metrics(sids))
    step("impute_v5", _with_conn(impute_v5_tables))
    step("data_quality", _with_conn(detect_and_write))
    if not opts["skip_volatility_forecast"]:
        step("volatility_forecast", _with_conn(sync_forecast))
    if not opts["skip_eps_revision"]:
        step("eps_revision", _eps)
    if not opts["skip_events"]:
        step("event_classification", _events)
    if not opts["skip_risk"]:
        step("risk_flags", lambda: scan_risk_flags(sids))
    if not opts["skip_policy"]:
        step("policy_v5", sync_policy_v5)
    if not opts["skip_mood"]:
        step("mood_v5", lambda: compute_all_mood_v5(sids))
    if not opts["skip_v5_scores"]:
        step("v5_scores", lambda: compute_all_v5_scores(sids))
    if not opts["skip_tushare_events"]:
        step("tushare_events", lambda: sync_tushare_event_data(
            stock_ids=sids,
            days=opts["tushare_event_days"],
            skip_per_stock=opts.get("skip_per_stock", True),
        ))

    t_cov = time.time()
    result["news_event_coverage"] = _news_event_coverage(opts["stock_ids"])
    log(f"· 新闻覆盖统计 {time.time() - t_cov:.0f}s {result['news_event_coverage']}")
    result["ok"] = all(
        "error" not in (v or {}) for v in result["steps"].values()
    )
    return result


def sync_v5_daily(stock_ids: list[int] | None = None) -> dict:
    """收盘后日常：跳过抓取，规则分类，仅缺新闻面股票跑 LLM，重算 V5。"""
    return sync_v5_data_sources(stock_ids=stock_ids, mode="daily")


def sync_v5_nightly_fetch(stock_ids: list[int] | None = None, log=None) -> dict:
    """夜间：抓取公告/新闻 + 全量规则分类，不重算 V5 分数。"""
    result = sync_v5_data_sources(stock_ids=stock_ids, mode="nightly", log=log)
    # 全局政策快照（DeepSeek 分批扫描）。P14:原来放在夜间最后一步,但夜间从没跑完过,
    # 实际自 2026-08-15 起没执行;LLM 费用待评估,默认关闭(config.NIGHTLY_GLOBAL_POLICY_SCAN)。
    if config.NIGHTLY_GLOBAL_POLICY_SCAN:
        from services.policy_global import global_policy_scan

        _StepRunner(result, log or noop_log)("policy_snapshot", global_policy_scan)
        result["policy_snapshot"] = result["steps"].pop("policy_snapshot")
    else:
        result["policy_snapshot"] = {"skipped": "AFR_NIGHTLY_GLOBAL_POLICY_SCAN 未开启"}
    return result


def sync_v5_weekly(stock_ids: list[int] | None = None) -> dict:
    """每周：EPS 修正 + 行业上修 + v5_metrics + 宏观 + 主力流 + V5 分数 + L2 矩阵。"""
    result = sync_v5_data_sources(stock_ids=stock_ids, mode="weekly")
    extra: dict = {}
    try:
        from services.lhb_sync import sync_lhb_watchlist

        extra["lhb_watchlist"] = sync_lhb_watchlist(stock_ids)
    except Exception as e:
        extra["lhb_watchlist"] = {"error": str(e)}

    # ETF 池清洗(一级分类 + 相关性聚类去重)。周频足够——ETF 列表变动少,daily 已滚动
    # 累积行情;失败不阻塞。子进程隔离(脚本自带 pandas 聚类,可能较重)。
    try:
        import os
        import subprocess
        import sys

        script = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "etf_build_pool.py")
        r = subprocess.run(
            [sys.executable, script], capture_output=True, text=True, timeout=600
        )
        if r.returncode == 0:
            extra["etf_pool"] = (r.stdout or "").strip().split("\n")[-1][:120]
        else:
            extra["etf_pool"] = {"error": (r.stderr or "")[-120:]}
    except Exception as e:
        extra["etf_pool"] = {"error": str(e)}

    # ETF 资金流计算(地基,行业/主题打分权重最高)。在打分之前跑,让 score_flow 拿到最新值。
    try:
        import os
        import subprocess
        import sys

        script = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "etf_flow_calc.py")
        r = subprocess.run([sys.executable, script], capture_output=True, text=True, timeout=600)
        extra["etf_flow"] = (r.stdout or "").strip().split("\n")[0][:120] if r.returncode == 0 else {"error": (r.stderr or "")[-120:]}
    except Exception as e:
        extra["etf_flow"] = {"error": str(e)}

    # ETF 持仓加权估值(填宽基/主题估值桩)。持仓季度缓存,拉一次约2-3分钟;失败不阻塞。
    try:
        import os
        import subprocess
        import sys

        script = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "etf_valuation_calc.py")
        r = subprocess.run([sys.executable, script], capture_output=True, text=True, timeout=900)
        extra["etf_valuation"] = (r.stdout or "").strip().split("\n")[0][:120] if r.returncode == 0 else {"error": (r.stderr or "")[-120:]}
    except Exception as e:
        extra["etf_valuation"] = {"error": str(e)}

    # ETF 分层打分(第二步)。必须在池清洗+资金流之后——只对 is_repr=1 的代表打分。失败不阻塞。
    try:
        import os
        import subprocess
        import sys

        script = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "etf_scoring.py")
        r = subprocess.run(
            [sys.executable, script], capture_output=True, text=True, timeout=600
        )
        if r.returncode == 0:
            extra["etf_score"] = (r.stdout or "").strip().split("\n")[0][:120]
        else:
            extra["etf_score"] = {"error": (r.stderr or "")[-120:]}
    except Exception as e:
        extra["etf_score"] = {"error": str(e)}

    # A股实时接口健康巡检(akshare/东财常悄改接口→静默返空)。返回码非0=有失效。不阻塞。
    try:
        import os
        import subprocess
        import sys

        script = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "health_check_astock.py")
        r = subprocess.run([sys.executable, script], capture_output=True, text=True, timeout=600)
        tail = (r.stdout or "").strip().split("\n")[-1][:120]
        extra["astock_health"] = tail if r.returncode == 0 else {"⚠️失效": tail}
    except Exception as e:
        extra["astock_health"] = {"error": str(e)}

    result["weekly_extra"] = extra
    return result
