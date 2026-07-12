"""批量维度补算编排"""
from __future__ import annotations

import time
from typing import Literal

import config
from services.batch_score_compute import COMPUTE_HANDLERS, compute_technical
from services.batch_score_guard import batch_fill_session
from services.batch_score_plan import MODE_PHASES, build_fill_plan
from services.comprehensive import sync_all_dimensions
from services.score_gap_log import log_fill_done, log_fill_error, log_fill_start
from services.score_gap_scanner import scan_gaps

FillMode = Literal["sync_only", "compute_and_sync", "force_recompute"]
P2_PHASES = frozenset({"capital_score", "mood_score", "policy_score", "val_score"})


def _active_stock_ids(stock_ids: list[int] | None) -> list[int]:
    if stock_ids:
        return stock_ids
    import sqlite3

    conn = sqlite3.connect(config.DB_PATH)
    try:
        rows = conn.execute("SELECT id FROM stocks WHERE is_active=1 ORDER BY id").fetchall()
        return [int(r[0]) for r in rows]
    finally:
        conn.close()


def _resolve_compute_ids(
    mode: str,
    phase: str,
    gap_targets: dict[str, list[int]],
    stock_ids: list[int] | None,
    all_active: list[int],
) -> list[int] | None:
    if mode == "force_recompute":
        return stock_ids or all_active
    ids = gap_targets.get(phase) or []
    return ids if ids else None


def fill_gaps(
    *,
    mode: FillMode = "sync_only",
    dimensions: list[str] | None = None,
    stock_ids: list[int] | None = None,
    target_date: str | None = None,
    skip_no_source: bool = True,
    dry_run: bool = False,
    job_id: str | None = None,
    triggered_by: str = "api",
) -> dict:
    if dry_run:
        return build_fill_plan(
            mode=mode,
            target_date=target_date,
            stock_ids=stock_ids,
            dimensions=dimensions,
            skip_no_source=skip_no_source,
        )

    t0 = time.perf_counter()
    all_active = _active_stock_ids(stock_ids)

    with batch_fill_session():
        before = scan_gaps(target_date=target_date, stock_ids=stock_ids, dimensions=dimensions)
        calc_date = target_date or before["target_date"]

        if job_id:
            log_fill_start(
                job_id,
                mode=mode,
                target_date=calc_date,
                before=before,
                stock_ids=stock_ids,
                triggered_by=triggered_by,
            )

        plan = build_fill_plan(
            mode=mode,
            target_date=calc_date,
            stock_ids=stock_ids,
            dimensions=dimensions,
            skip_no_source=skip_no_source,
        )
        gap_targets = plan.get("gap_targets", {})
        actions: dict = {}
        errors: list[dict] = []

        if mode in ("compute_and_sync", "force_recompute"):
            from services.score_gap_prefetch import execute_prefetch_for_gaps

            prefetch_actions = execute_prefetch_for_gaps(before, max_batch=30)
            if prefetch_actions:
                actions["prefetch"] = prefetch_actions
                before = scan_gaps(
                    target_date=calc_date, stock_ids=stock_ids, dimensions=dimensions
                )
                plan = build_fill_plan(
                    mode=mode,
                    target_date=calc_date,
                    stock_ids=stock_ids,
                    dimensions=dimensions,
                    skip_no_source=skip_no_source,
                )
                gap_targets = plan.get("gap_targets", {})

        try:
            phases = MODE_PHASES.get(mode, MODE_PHASES["sync_only"])
            idx = 0
            while idx < len(phases):
                phase = phases[idx]

                if phase == "sync_all_dimensions":
                    sync_result = sync_all_dimensions(
                        stock_ids=stock_ids,
                        calc_date=calc_date,
                        overwrite=False,
                        refresh_stale=True,
                    )
                    actions["sync"] = sync_result
                    idx += 1
                    continue

                if phase in P2_PHASES:
                    p2_batch: list[str] = []
                    while idx < len(phases) and phases[idx] in P2_PHASES:
                        p2_batch.append(phases[idx])
                        idx += 1
                    p2_phases: list[str] = []
                    p2_ids: set[int] = set()
                    for p in p2_batch:
                        ids = _resolve_compute_ids(
                            mode, p, gap_targets, stock_ids, all_active
                        )
                        if ids:
                            p2_phases.append(p)
                            p2_ids.update(ids)
                    if p2_phases and p2_ids:
                        from config import P2_PARALLEL_ENABLED
                        from services.batch_score_maintenance import run_p2_phases_parallel

                        if P2_PARALLEL_ENABLED:
                            p2_results = run_p2_phases_parallel(
                                sorted(p2_ids), calc_date, tuple(p2_phases)
                            )
                        else:
                            p2_results = {}
                            for p in p2_phases:
                                handler = COMPUTE_HANDLERS.get(p)
                                if handler:
                                    p2_results[p] = handler(sorted(p2_ids), calc_date)
                        for p, result in p2_results.items():
                            actions[p] = result
                            if isinstance(result, dict) and result.get("errors"):
                                errors.extend(result["errors"])
                    continue

                compute_ids = _resolve_compute_ids(
                    mode, phase, gap_targets, stock_ids, all_active
                )
                if not compute_ids:
                    idx += 1
                    continue

                if phase == "technical_score":
                    from services.job_queue import touch_job_heartbeat

                    def _heartbeat() -> None:
                        if job_id:
                            touch_job_heartbeat(job_id)

                    result = compute_technical(
                        compute_ids,
                        calc_date,
                        heartbeat=_heartbeat if job_id else None,
                    )
                    actions[phase] = result
                    errors.extend(result.get("failed_stocks", []))
                    idx += 1
                    continue

                if phase == "sentiment_score" and compute_ids:
                    from services.score_gap_fetch import fetch_sentiment_for_gaps

                    actions["prefetch_sentiment"] = fetch_sentiment_for_gaps(
                        before,
                        stock_ids=compute_ids,
                        include_stale=True,
                    )

                handler = COMPUTE_HANDLERS.get(phase)
                if handler:
                    result = handler(compute_ids, calc_date)
                    actions[phase] = result
                    if result.get("errors"):
                        errors.extend(result["errors"])
                idx += 1

            if mode in ("compute_and_sync", "force_recompute"):
                actions["sync_end"] = sync_all_dimensions(
                    stock_ids=stock_ids,
                    calc_date=calc_date,
                    overwrite=False,
                    refresh_stale=True,
                )

            after = scan_gaps(target_date=calc_date, stock_ids=stock_ids, dimensions=dimensions)
            duration_ms = int((time.perf_counter() - t0) * 1000)

            if job_id:
                log_fill_done(
                    job_id,
                    mode=mode,
                    target_date=calc_date,
                    before=before,
                    after=after,
                    actions=actions,
                    duration_ms=duration_ms,
                    triggered_by=triggered_by,
                )

            try:
                from services.score_health_monitor import maybe_send_recovery

                maybe_send_recovery(after, job_id=job_id)
            except Exception:
                pass

            return {
                "mode": mode,
                "job_id": job_id,
                "target_date": calc_date,
                "before": before,
                "after": after,
                "actions": actions,
                "errors": errors,
                "duration_ms": duration_ms,
                "status": "done",
            }
        except Exception as e:
            if job_id:
                try:
                    log_fill_error(
                        job_id,
                        target_date=calc_date,
                        mode=mode,
                        error=str(e),
                        triggered_by=triggered_by,
                    )
                except Exception:
                    pass
            raise
