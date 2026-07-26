"""V5 数据源一键同步 — Phase 1+2 + Phase 3 W1/W2（东财/腾讯/巨潮/自算）。"""
from __future__ import annotations

import sqlite3
from typing import Any

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
from services.market_regime import sync_regime
from services.ml_impute import impute_v5_tables
from services.volatility_forecast import sync_forecast
from services.quality_metrics_calc import compute_all_v5_metrics
from services.risk_scanner import scan_risk_flags
from services.sector_fund_flow_sync import sync_sector_fund_flow
from services.tushare_event_sync import sync_tushare_event_data
from services.v5_scorer import compute_all_v5_scores

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
        "use_llm_events": True,
        "llm_only_missing_news": False,
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
    },
}


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
    announcement_limit: int = 30,
    news_limit: int = 15,
    skip_volatility_forecast: bool = False,
) -> dict:
    """同步 V5 评分数据源（含公告/新闻抓取、事件分类、EPS/风险/政策/情绪）。"""
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

    if not opts["skip_announcements"]:
        try:
            result["steps"]["announcements"] = sync_all_announcements(
                opts["stock_ids"], limit=opts["announcement_limit"]
            )
        except Exception as e:
            result["steps"]["announcements"] = {"error": str(e)}

    if not opts["skip_news_fetch"]:
        try:
            result["steps"]["news_fetch"] = sync_all_news(
                opts["stock_ids"], limit=opts["news_limit"]
            )
        except Exception as e:
            result["steps"]["news_fetch"] = {"error": str(e)}

    if not opts["skip_macro"]:
        try:
            result["steps"]["macro"] = sync_macro_indicators()
        except Exception as e:
            result["steps"]["macro"] = {"error": str(e)}

    try:
        with sqlite3.connect(config.DB_PATH, timeout=120) as conn:
            conn.execute("PRAGMA busy_timeout=30000")
            result["steps"]["market_regime"] = sync_regime(conn)
    except Exception as e:
        result["steps"]["market_regime"] = {"error": str(e)}

    if not opts["skip_sector"]:
        try:
            result["steps"]["sector_fund_flow"] = sync_sector_fund_flow()
        except Exception as e:
            result["steps"]["sector_fund_flow"] = {"error": str(e)}

    if not opts["skip_fund_flow"]:
        try:
            result["steps"]["stock_fund_flow"] = sync_stock_fund_flow(opts["stock_ids"])
        except Exception as e:
            result["steps"]["stock_fund_flow"] = {"error": str(e)}

    if not opts["skip_industry_l2"]:
        try:
            result["steps"]["industry_l2"] = sync_industry_l2(opts["stock_ids"])
        except Exception as e:
            result["steps"]["industry_l2"] = {"error": str(e)}

    if not opts["skip_metrics"]:
        try:
            result["steps"]["v5_metrics"] = compute_all_v5_metrics(opts["stock_ids"])
        except Exception as e:
            result["steps"]["v5_metrics"] = {"error": str(e)}

    try:
        with sqlite3.connect(config.DB_PATH, timeout=120) as conn:
            conn.execute("PRAGMA busy_timeout=30000")
            result["steps"]["impute_v5"] = impute_v5_tables(conn)
    except Exception as e:
        result["steps"]["impute_v5"] = {"error": str(e)}

    try:
        with sqlite3.connect(config.DB_PATH, timeout=120) as conn:
            conn.execute("PRAGMA busy_timeout=30000")
            result["steps"]["data_quality"] = detect_and_write(conn)
    except Exception as e:
        result["steps"]["data_quality"] = {"error": str(e)}

    if not opts["skip_volatility_forecast"]:
        try:
            with sqlite3.connect(config.DB_PATH, timeout=120) as conn:
                conn.execute("PRAGMA busy_timeout=30000")
                result["steps"]["volatility_forecast"] = sync_forecast(conn)
        except Exception as e:
            result["steps"]["volatility_forecast"] = {"error": str(e)}

    if not opts["skip_eps_revision"]:
        try:
            eps = sync_stock_eps_forecast(opts["stock_ids"])
            eps["industry"] = sync_industry_eps_revision(
                trade_date=eps.get("as_of_date")
            )
            result["steps"]["eps_revision"] = eps
        except Exception as e:
            result["steps"]["eps_revision"] = {"error": str(e)}

    if not opts["skip_events"]:
        try:
            llm_targets = _resolve_llm_stock_ids(
                opts["stock_ids"],
                use_llm_events=opts["use_llm_events"],
                llm_only_missing_news=opts["llm_only_missing_news"],
            )
            use_llm = bool(opts["use_llm_events"] and llm_targets)
            result["steps"]["event_classification"] = sync_event_classification(
                opts["stock_ids"],
                reclassify=opts["reclassify_events"],
                use_llm=use_llm,
                llm_limit_per_stock=opts["llm_event_limit_per_stock"],
                llm_stock_ids=llm_targets if use_llm else [],
            )
            if opts["llm_only_missing_news"]:
                result["steps"]["event_classification"]["llm_missing_only"] = llm_targets
        except Exception as e:
            result["steps"]["event_classification"] = {"error": str(e)}

    if not opts["skip_risk"]:
        try:
            result["steps"]["risk_flags"] = scan_risk_flags(opts["stock_ids"])
        except Exception as e:
            result["steps"]["risk_flags"] = {"error": str(e)}

    if not opts["skip_policy"]:
        try:
            result["steps"]["policy_v5"] = sync_policy_v5()
        except Exception as e:
            result["steps"]["policy_v5"] = {"error": str(e)}

    if not opts["skip_mood"]:
        try:
            result["steps"]["mood_v5"] = compute_all_mood_v5(opts["stock_ids"])
        except Exception as e:
            result["steps"]["mood_v5"] = {"error": str(e)}

    if not opts["skip_v5_scores"]:
        try:
            result["steps"]["v5_scores"] = compute_all_v5_scores(opts["stock_ids"])
        except Exception as e:
            result["steps"]["v5_scores"] = {"error": str(e)}

    if not opts["skip_tushare_events"]:
        try:
            result["steps"]["tushare_events"] = sync_tushare_event_data(
                stock_ids=opts["stock_ids"],
                days=opts["tushare_event_days"],
                skip_per_stock=opts.get("skip_per_stock", True),
            )
        except Exception as e:
            result["steps"]["tushare_events"] = {"error": str(e)}

    result["news_event_coverage"] = _news_event_coverage(opts["stock_ids"])
    result["ok"] = all(
        "error" not in (v or {}) for v in result["steps"].values()
    )
    return result


def sync_v5_daily(stock_ids: list[int] | None = None) -> dict:
    """收盘后日常：跳过抓取，规则分类，仅缺新闻面股票跑 LLM，重算 V5。"""
    return sync_v5_data_sources(stock_ids=stock_ids, mode="daily")


def sync_v5_nightly_fetch(stock_ids: list[int] | None = None) -> dict:
    """夜间：抓取公告/新闻 + 全量规则/LLM 分类，不重算 V5 分数。"""
    return sync_v5_data_sources(stock_ids=stock_ids, mode="nightly")


def sync_v5_weekly(stock_ids: list[int] | None = None) -> dict:
    """每周：EPS 修正 + 行业上修 + v5_metrics + 宏观 + 主力流 + V5 分数。"""
    result = sync_v5_data_sources(stock_ids=stock_ids, mode="weekly")
    extra: dict = {}
    try:
        from services.lhb_sync import sync_lhb_watchlist

        extra["lhb_watchlist"] = sync_lhb_watchlist(stock_ids)
    except Exception as e:
        extra["lhb_watchlist"] = {"error": str(e)}
    result["weekly_extra"] = extra
    return result
