"""辩论批量编排 — plan / dry-run / job handler。"""
from __future__ import annotations

import time
from typing import Any, Literal

import config
from services.debate_batch_runner import run_debate_parallel
from services.debate_context import DebateBatchContext, preload_debate_context
from services.debate_retry import resolve_retry_stock_ids
from services.debate_tiered import assign_tier, compute_priority_ids
from services.debate_types import DebateTarget
from services.debate_v2 import should_skip_debate

DEBATE_BATCH_JOB_TYPE = "debate_batch"
DebateMode = Literal["full", "changed_only", "tiered", "force", "retry_failed"]


def _active_targets(ctx: DebateBatchContext) -> list[tuple[int, str]]:
    return [(sid, s["code"]) for sid, s in sorted(ctx.stocks.items())]


def plan_debate_batch(
    ctx: DebateBatchContext,
    *,
    mode: str = "full",
    skip_unchanged: bool | None = None,
    stock_ids: list[int] | None = None,
    priority_top_n: int | None = None,
    priority_bottom_n: int | None = None,
    retry_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    skip = config.DEBATE_SKIP_UNCHANGED if skip_unchanged is None else skip_unchanged
    if mode in ("force", "retry_failed", "retry"):
        skip = False

    targets = _active_targets(ctx)
    if stock_ids:
        allowed = set(stock_ids)
        targets = [(sid, code) for sid, code in targets if sid in allowed]

    plan_mode = "force" if mode in ("retry_failed", "retry") else mode
    priority_ids = compute_priority_ids(
        ctx.comprehensive,
        top_n=priority_top_n,
        bottom_n=priority_bottom_n,
    )

    llm_targets: list[DebateTarget] = []
    light_targets: list[DebateTarget] = []
    skipped: list[dict[str, Any]] = []
    skip_reasons: dict[str, int] = {}
    tier_counts: dict[str, int] = {}

    for sid, code in targets:
        comp = ctx.comprehensive.get(sid)
        if not comp:
            skipped.append({"stock_id": sid, "code": code, "reason": "no_comp_score"})
            skip_reasons["no_comp_score"] = skip_reasons.get("no_comp_score", 0) + 1
            continue

        if skip and should_skip_debate(ctx, sid):
            row = ctx.existing_debate.get(sid, {})
            skipped.append(
                {
                    "stock_id": sid,
                    "code": code,
                    "reason": "unchanged_today",
                    "adjusted_score": row.get("adjusted_score"),
                }
            )
            skip_reasons["unchanged_today"] = skip_reasons.get("unchanged_today", 0) + 1
            continue

        tier, use_llm = assign_tier(sid, mode=plan_mode, priority_ids=priority_ids, ctx=ctx)
        if mode == "retry_failed":
            tier = "retry_failed"
            use_llm = True
        item = DebateTarget(stock_id=sid, code=code, tier=tier, use_llm=use_llm)
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
        if use_llm:
            llm_targets.append(item)
        else:
            light_targets.append(item)

    all_run = llm_targets + light_targets
    llm_est = len(llm_targets) * config.DEBATE_EST_MS_PER_STOCK
    light_est = len(light_targets) * config.DEBATE_EST_MS_LIGHT
    est_ms = llm_est + light_est
    if config.DEBATE_CONCURRENCY > 0 and llm_targets:
        est_ms = int(light_est + llm_est / config.DEBATE_CONCURRENCY)

    def _serialize(items: list[DebateTarget]) -> list[dict[str, Any]]:
        return [
            {"stock_id": t.stock_id, "code": t.code, "tier": t.tier, "use_llm": t.use_llm}
            for t in items
        ]

    out = {
        "total": len(targets),
        "to_run": len(all_run),
        "llm_count": len(llm_targets),
        "light_count": len(light_targets),
        "skipped": len(skipped),
        "skip_reasons": skip_reasons,
        "tier_counts": tier_counts,
        "targets": _serialize(all_run),
        "llm_targets": _serialize(llm_targets),
        "light_targets": _serialize(light_targets),
        "skipped_items": skipped,
        "mode": mode,
        "calc_date": ctx.calc_date,
        "today": ctx.today,
        "est_ms": est_ms,
        "concurrency": config.DEBATE_CONCURRENCY,
        "priority_top_n": priority_top_n or config.DEBATE_PRIORITY_TOP_N,
        "priority_bottom_n": priority_bottom_n or config.DEBATE_PRIORITY_BOTTOM_N,
    }
    if retry_info:
        out["retry_info"] = retry_info
    return out


def _merge_results_by_stock(results: list[dict]) -> list[dict]:
    merged: dict[int, dict] = {}
    for item in results:
        sid = item.get("stock_id")
        if sid is None:
            continue
        prev = merged.get(sid)
        if prev is None or (prev.get("error") and not item.get("error")):
            merged[sid] = item
    return list(merged.values())


def _retry_failed_targets(results: list[dict], ctx: DebateBatchContext) -> list[DebateTarget]:
    targets: list[DebateTarget] = []
    for item in results:
        if not item.get("error"):
            continue
        sid = int(item["stock_id"])
        stock = ctx.stocks.get(sid)
        if not stock:
            continue
        targets.append(
            DebateTarget(stock_id=sid, code=stock["code"], tier="retry", use_llm=True)
        )
    return targets


def run_debate_batch(
    *,
    mode: str | None = None,
    stock_ids: list[int] | None = None,
    concurrency: int | None = None,
    skip_unchanged: bool | None = None,
    write_composite: bool | None = None,
    priority_top_n: int | None = None,
    priority_bottom_n: int | None = None,
    retry_job_id: str | None = None,
    dry_run: bool = False,
    job_id: str | None = None,
    triggered_by: str = "api",
) -> dict[str, Any]:
    t0 = time.perf_counter()
    resolved_mode = mode or config.DEBATE_DEFAULT_MODE
    allowed_modes = ("full", "changed_only", "tiered", "force", "retry_failed")
    if resolved_mode not in allowed_modes:
        resolved_mode = config.DEBATE_DEFAULT_MODE

    retry_info: dict[str, Any] | None = None
    if resolved_mode == "retry_failed":
        retry_info = resolve_retry_stock_ids(retry_job_id, stock_ids=stock_ids)
        stock_ids = retry_info.get("stock_ids") or []
        skip_unchanged = False
        if not stock_ids:
            return {
                "job_id": job_id,
                "triggered_by": triggered_by,
                "mode": resolved_mode,
                "message": retry_info.get("message") or "无失败项可补跑",
                "retry_info": retry_info,
                "total": 0,
                "to_run": 0,
                "completed": 0,
                "errors": [],
                "results": [],
                "duration_ms": int((time.perf_counter() - t0) * 1000),
            }

    ctx = preload_debate_context(stock_ids)

    plan = plan_debate_batch(
        ctx,
        mode=resolved_mode,
        skip_unchanged=skip_unchanged,
        stock_ids=stock_ids,
        priority_top_n=priority_top_n,
        priority_bottom_n=priority_bottom_n,
        retry_info=retry_info,
    )
    if dry_run:
        plan["dry_run"] = True
        plan["triggered_by"] = triggered_by
        return plan

    from services.debate_batch_log import log_batch_done, log_batch_start

    log_batch_start(
        job_id=job_id,
        mode=resolved_mode,
        plan=plan,
        triggered_by=triggered_by,
    )

    run_targets = [
        DebateTarget(
            stock_id=t["stock_id"],
            code=t["code"],
            tier=t.get("tier", "full_llm"),
            use_llm=bool(t.get("use_llm", True)),
        )
        for t in plan["targets"]
    ]

    progress = {
        "completed": 0,
        "total": plan["to_run"],
        "llm_total": plan["llm_count"],
        "light_total": plan["light_count"],
        "skipped": plan["skipped"],
        "errors": 0,
    }

    def _on_result(item: dict) -> None:
        progress["completed"] += 1
        if item.get("error"):
            progress["errors"] += 1
        if job_id:
            from services.job_queue import update_job_progress

            update_job_progress(job_id, {"plan": plan, "progress": dict(progress)})

    heartbeat = None
    if job_id:
        from services.job_queue import touch_job_heartbeat

        heartbeat = lambda: touch_job_heartbeat(job_id)

    results = run_debate_parallel(
        run_targets,
        ctx,
        concurrency=concurrency,
        skip_unchanged=False,
        write_composite=write_composite,
        heartbeat=heartbeat,
        on_result=_on_result,
    )

    batch_retry_passes = 0
    while config.DEBATE_BATCH_RETRY_PASS > 0 and batch_retry_passes < config.DEBATE_BATCH_RETRY_PASS:
        failed_targets = _retry_failed_targets(results, ctx)
        if not failed_targets:
            break
        batch_retry_passes += 1
        retry_results = run_debate_parallel(
            failed_targets,
            ctx,
            concurrency=concurrency,
            skip_unchanged=False,
            write_composite=write_composite,
            heartbeat=heartbeat,
        )
        results = _merge_results_by_stock(results + retry_results)

    errors = [r for r in results if r.get("error")]
    duration_ms = int((time.perf_counter() - t0) * 1000)

    out = {
        "job_id": job_id,
        "triggered_by": triggered_by,
        "plan": plan,
        "total": plan["total"],
        "completed": len(results),
        "llm_count": plan["llm_count"],
        "light_count": plan["light_count"],
        "skipped": plan["skipped"],
        "skipped_items": plan["skipped_items"],
        "batch_retry_passes": batch_retry_passes,
        "errors": errors,
        "results": results,
        "duration_ms": duration_ms,
    }
    log_batch_done(
        job_id=job_id,
        mode=resolved_mode,
        plan=plan,
        result=out,
        triggered_by=triggered_by,
    )
    return out


def run_debate_batch_job(payload: dict) -> dict:
    import database as db

    if not db.is_initialized():
        db.init()
    return run_debate_batch(
        mode=payload.get("mode"),
        stock_ids=payload.get("stock_ids"),
        concurrency=payload.get("concurrency"),
        skip_unchanged=payload.get("skip_unchanged"),
        write_composite=payload.get("write_composite"),
        priority_top_n=payload.get("priority_top_n"),
        priority_bottom_n=payload.get("priority_bottom_n"),
        retry_job_id=payload.get("retry_job_id"),
        job_id=payload.get("job_id"),
        triggered_by=payload.get("triggered_by", "job_queue"),
    )
