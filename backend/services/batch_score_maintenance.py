"""technical no_source 低优先级重试 + fetch 后 gap 同步"""
from __future__ import annotations

import logging
from typing import Any

import config
from services.batch_score_guard import can_run_sync

logger = logging.getLogger("afr.batch_score_maintenance")

P2_PARALLEL_PHASES = ("capital_score", "mood_score", "policy_score", "val_score")


def retry_sentiment_gaps(target_date: str | None = None) -> dict[str, Any]:
    """sentiment no_source / stale：拉新闻 + 规则/LLM 评分。"""
    ok, reason = can_run_sync()
    if not ok:
        return {"skipped": True, "reason": reason}

    from services.score_gap_fetch import fetch_sentiment_for_gaps
    from services.batch_score_compute import compute_sentiment_news
    from services.comprehensive import sync_all_dimensions
    from services.score_gap_scanner import scan_gaps

    before = scan_gaps(target_date=target_date)
    fetch_result = fetch_sentiment_for_gaps(before, include_stale=True)
    ids = sorted(
        {
            int(g["stock_id"])
            for g in before.get("gaps", [])
            if g.get("dimension") == "sentiment_score"
            and g.get("status") in ("no_source", "missing", "stale")
        }
    )
    compute_result = compute_sentiment_news(ids or None, before["target_date"])
    sync_result = sync_all_dimensions(
        stock_ids=ids or None,
        calc_date=before["target_date"],
        overwrite=False,
        refresh_stale=True,
    )
    after = scan_gaps(target_date=before["target_date"])
    return {
        "target_date": before["target_date"],
        "fetch": fetch_result,
        "compute": compute_result,
        "sync": sync_result,
        "before_stale": before.get("stale_total"),
        "after_stale": after.get("stale_total"),
        "after_sync_rate_all": after.get("sync_rate_all"),
    }


def retry_technical_no_source(target_date: str | None = None) -> dict[str, Any]:
    """对 technical no_source 股票低优先级重试（Phase 3）。"""
    ok, reason = can_run_sync()
    if not ok:
        return {"skipped": True, "reason": reason}

    from services.batch_score_compute import compute_technical
    from services.score_gap_scanner import scan_gaps

    gaps = scan_gaps(target_date=target_date)
    ids = sorted(
        {
            int(g["stock_id"])
            for g in gaps.get("gaps", [])
            if g.get("dimension") == "technical_score" and g.get("status") == "no_source"
        }
    )
    if not ids:
        return {"skipped": True, "reason": "no technical no_source", "target_date": gaps["target_date"]}

    logger.info("[TechnicalRetry] %d stocks target_date=%s", len(ids), gaps["target_date"])
    result = compute_technical(ids, gaps["target_date"])
    return {
        "target_date": gaps["target_date"],
        "attempted": len(ids),
        **result,
    }


def sync_gaps_after_fetch(target_date: str | None = None) -> dict[str, Any]:
    """数据抓取 / 评分重算后：有缺口则 sync_only（Phase 3 fetch 联动）。"""
    ok, reason = can_run_sync()
    if not ok:
        return {"skipped": True, "reason": reason}

    from services.comprehensive import sync_all_dimensions
    from services.score_gap_scanner import scan_gaps

    gaps = scan_gaps(target_date=target_date)
    calc_date = gaps["target_date"]
    missing = gaps.get("missing_total", 0)
    stale = gaps.get("stale_total", 0)
    if missing <= 0 and stale <= 0:
        return {"skipped": True, "reason": "no gaps", "target_date": calc_date}

    sync_result = sync_all_dimensions(
        stock_ids=None,
        calc_date=calc_date,
        overwrite=False,
        refresh_stale=True,
    )
    after = scan_gaps(target_date=calc_date)
    return {
        "target_date": calc_date,
        "before_missing": missing,
        "before_stale": stale,
        "sync": sync_result,
        "after_sync_rate_required": after.get("sync_rate_required"),
    }


def run_p2_phases_parallel(
    compute_ids: list[int],
    calc_date: str,
    phases: tuple[str, ...] = P2_PARALLEL_PHASES,
) -> dict[str, dict]:
    """P2 四维度并行 compute（Phase 3）。"""
    import asyncio

    from services.batch_score_compute import COMPUTE_HANDLERS

    async def _run() -> dict[str, dict]:
        tasks: list[tuple[str, Any]] = []
        for phase in phases:
            handler = COMPUTE_HANDLERS.get(phase)
            if handler:
                tasks.append((phase, asyncio.to_thread(handler, compute_ids, calc_date)))
        if not tasks:
            return {}
        results = await asyncio.gather(*[t[1] for t in tasks], return_exceptions=True)
        out: dict[str, dict] = {}
        for (phase, _), result in zip(tasks, results):
            if isinstance(result, Exception):
                out[phase] = {"error": str(result)}
            else:
                out[phase] = result
        return out

    return asyncio.run(_run())
