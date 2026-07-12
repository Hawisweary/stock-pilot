"""辩论批量并发执行 — ThreadPoolExecutor + 进度回调 + 429 降并发。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Union

import config
from services.debate_context import DebateBatchContext
from services.debate_types import DebateTarget
from services.debate_v2 import enhanced_debate_with_context
from services.llm_client import _is_rate_limit

TargetInput = Union[DebateTarget, tuple[int, str]]


def _normalize_target(item: TargetInput) -> DebateTarget:
    if isinstance(item, DebateTarget):
        return item
    sid, code = item
    return DebateTarget(stock_id=sid, code=code, tier="full_llm", use_llm=True)


def _is_rate_limit_msg(msg: str | None) -> bool:
    if not msg:
        return False
    return _is_rate_limit(RuntimeError(str(msg)))


def _execute_wave(
    targets: list[DebateTarget],
    ctx: DebateBatchContext,
    *,
    workers: int,
    skip_unchanged: bool | None,
    write_composite: bool | None,
    heartbeat: Callable[[], None] | None,
    on_result: Callable[[dict], None] | None,
) -> list[dict]:
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {
            pool.submit(
                enhanced_debate_with_context,
                ctx,
                t.stock_id,
                t.code,
                skip_unchanged=skip_unchanged,
                write_composite=write_composite,
                use_llm=t.use_llm,
                tier=t.tier,
            ): t
            for t in targets
        }
        for i, fut in enumerate(as_completed(futs)):
            target = futs[fut]
            try:
                item = fut.result()
            except Exception as e:
                item = {
                    "stock_id": target.stock_id,
                    "code": target.code,
                    "tier": target.tier,
                    "error": str(e),
                }
            results.append(item)
            if on_result:
                on_result(item)
            if heartbeat and i % 2 == 0:
                heartbeat()
    return results


def run_debate_parallel(
    targets: list[TargetInput],
    ctx: DebateBatchContext,
    *,
    concurrency: int | None = None,
    skip_unchanged: bool | None = None,
    write_composite: bool | None = None,
    heartbeat: Callable[[], None] | None = None,
    on_result: Callable[[dict], None] | None = None,
) -> list[dict]:
    if not targets:
        return []

    normalized = [_normalize_target(t) for t in targets]
    workers = concurrency if concurrency is not None else config.DEBATE_CONCURRENCY
    workers = max(1, min(workers, len(normalized)))

    results = _execute_wave(
        normalized,
        ctx,
        workers=workers,
        skip_unchanged=skip_unchanged,
        write_composite=write_composite,
        heartbeat=heartbeat,
        on_result=on_result,
    )

    if (
        config.DEBATE_AUTO_DEGRADE_CONCURRENCY
        and workers > 1
        and any(_is_rate_limit_msg(r.get("error")) for r in results)
    ):
        degraded = max(1, workers // 2)
        retry_targets = [
            normalized[i]
            for i, r in enumerate(results)
            if r.get("error") and _is_rate_limit_msg(r.get("error"))
        ]
        if retry_targets:
            retry_results = _execute_wave(
                retry_targets,
                ctx,
                workers=degraded,
                skip_unchanged=False,
                write_composite=write_composite,
                heartbeat=heartbeat,
                on_result=on_result,
            )
            by_id = {r["stock_id"]: r for r in results if r.get("stock_id") is not None}
            for item in retry_results:
                sid = item.get("stock_id")
                if sid is None:
                    continue
                prev = by_id.get(sid)
                if prev is None or (prev.get("error") and not item.get("error")):
                    by_id[sid] = item
            results = list(by_id.values())

    return results
