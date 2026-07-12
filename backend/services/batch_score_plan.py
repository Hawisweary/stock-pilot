"""batch-fill dry-run 计划与估时"""
from __future__ import annotations

from typing import Any

import config
from services.score_gap_scanner import ALL_SYNC_DIMENSIONS, scan_gaps

BATCH_FILL_JOB_TYPE = "batch_score_fill"

DIMENSION_BASE_MS: dict[str, int] = {
    "sync_all_dimensions": 300,
    "fundamental_score": 3000,
    "capital_score": 140,
    "mood_score": 120,
    "policy_score": 2000,
    "val_score": 800,
    "sentiment_score": 5000,
    "technical_score": 1100,
}

ACTION_PRIORITY: dict[str, int] = {
    "sync_all_dimensions": 0,
    "fundamental_score": 1,
    "capital_score": 2,
    "mood_score": 2,
    "policy_score": 2,
    "val_score": 2,
    "sentiment_score": 3,
    "technical_score": 4,
}

MODE_PHASES: dict[str, list[str]] = {
    "sync_only": ["sync_all_dimensions"],
    "compute_and_sync": [
        "sync_all_dimensions",
        "fundamental_score",
        "capital_score",
        "mood_score",
        "policy_score",
        "val_score",
        "sentiment_score",
        "technical_score",
    ],
    "force_recompute": [
        "fundamental_score",
        "capital_score",
        "mood_score",
        "policy_score",
        "val_score",
        "sentiment_score",
        "technical_score",
    ],
}


def estimate_action_ms(
    action: str,
    affected_stocks: int,
    *,
    active_stocks: int = 54,
    would_fetch_count: int = 0,
) -> dict[str, Any]:
    base = DIMENSION_BASE_MS.get(action, 1000)
    pool = max(active_stocks, 1)
    linear = int(base * max(affected_stocks, 1) / pool)
    if action == "technical_score":
        fetch_penalty = would_fetch_count * 2500
        return {
            "estimated_ms": linear + fetch_penalty,
            "estimated_ms_range": [linear, linear + fetch_penalty + 30000],
        }
    return {"estimated_ms": linear, "estimated_ms_range": [int(linear * 0.8), int(linear * 1.3)]}


def _gap_targets(gap_report: dict, *, skip_no_source: bool = True) -> dict[str, set[int]]:
    targets: dict[str, set[int]] = {d: set() for d in ALL_SYNC_DIMENSIONS}
    for gap in gap_report.get("gaps", []):
        dim = gap.get("dimension")
        status = gap.get("status")
        if dim not in targets:
            continue
        if status == "missing":
            targets[dim].add(int(gap["stock_id"]))
        elif status == "stale":
            targets[dim].add(int(gap["stock_id"]))
        elif status == "no_source" and not skip_no_source:
            targets[dim].add(int(gap["stock_id"]))
    return targets


def probe_technical_quotes(stock_ids: list[int]) -> dict[str, Any]:
    """轻量探测：近 60 日行情是否 ≥20 条。"""
    import sqlite3

    if not stock_ids:
        return {"attempted": 0, "would_fetch": 0, "would_skip": 0, "details": []}
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    would_fetch = 0
    details: list[dict] = []
    try:
        for sid in stock_ids:
            row = conn.execute(
                """
                SELECT COUNT(*) AS c FROM stock_daily_quotes
                WHERE stock_id=? AND trade_date >= date('now', '-60 days')
                """,
                (sid,),
            ).fetchone()
            count = int(row["c"]) if row else 0
            need_fetch = count < 20
            if need_fetch:
                would_fetch += 1
                details.append(
                    {
                        "stock_id": sid,
                        "dimension": "technical_score",
                        "reason": "quotes<20",
                        "would_fetch": True,
                        "quote_count": count,
                    }
                )
        return {
            "attempted": len(stock_ids),
            "would_fetch": would_fetch,
            "would_succeed": max(0, len(stock_ids) - would_fetch),
            "would_fail": would_fetch,
            "details": details,
        }
    finally:
        conn.close()


def build_fill_plan(
    *,
    mode: str = "compute_and_sync",
    target_date: str | None = None,
    stock_ids: list[int] | None = None,
    dimensions: list[str] | None = None,
    skip_no_source: bool = True,
) -> dict[str, Any]:
    gaps = scan_gaps(target_date=target_date, stock_ids=stock_ids, dimensions=dimensions)
    target = gaps["target_date"]
    active = gaps["active_stocks_count"]
    targets = _gap_targets(gaps, skip_no_source=skip_no_source)
    phases = MODE_PHASES.get(mode, MODE_PHASES["compute_and_sync"])

    planned: list[dict] = []
    would_skip: list[dict] = []
    total_low = 0
    total_high = 0

    import sqlite3 as _sqlite3

    _conn = _sqlite3.connect(config.DB_PATH, timeout=120)
    all_active = [
        int(r[0])
        for r in _conn.execute("SELECT id FROM stocks WHERE is_active=1 ORDER BY id").fetchall()
    ]
    _conn.close()

    for action in phases:
        would_fetch = 0
        if action == "sync_all_dimensions":
            affected = gaps.get("missing_total", 0)
            if affected <= 0 and mode in ("sync_only", "compute_and_sync"):
                affected = active
            est = estimate_action_ms(action, max(affected, 1), active_stocks=active)
        elif mode == "force_recompute":
            affected = len(stock_ids) if stock_ids else active
            if action == "technical_score":
                probe = probe_technical_quotes(stock_ids or all_active)
                would_fetch = probe["would_fetch"]
                would_skip.extend(probe["details"])
            est = estimate_action_ms(
                action,
                max(affected, 1),
                active_stocks=active,
                would_fetch_count=would_fetch,
            )
            planned.append(
                {
                    "priority": ACTION_PRIORITY.get(action, 99),
                    "action": action,
                    "affected_stocks": affected,
                    **est,
                    **({"would_fetch": would_fetch} if action == "technical_score" else {}),
                }
            )
            total_low += est["estimated_ms_range"][0]
            total_high += est["estimated_ms_range"][1]
            continue
        else:
            affected_ids = targets.get(action, set())
            affected = len(affected_ids)
            if action == "technical_score" and affected_ids:
                probe = probe_technical_quotes(list(affected_ids))
                would_fetch = probe["would_fetch"]
                would_skip.extend(probe["details"])
            est = estimate_action_ms(
                action,
                max(affected, 1),
                active_stocks=active,
                would_fetch_count=would_fetch,
            )
            if affected == 0:
                would_skip.append({"action": action, "reason": "no_gaps"})
                continue

        planned.append(
            {
                "priority": ACTION_PRIORITY.get(action, 99),
                "action": action,
                "affected_stocks": affected,
                **est,
                **({"would_fetch": would_fetch} if action == "technical_score" else {}),
            }
        )
        total_low += est["estimated_ms_range"][0]
        total_high += est["estimated_ms_range"][1]

    prefetch_probe = (
        probe_technical_quotes(list(targets.get("technical_score", set())))
        if targets.get("technical_score")
        else {"attempted": 0, "would_fetch": 0}
    )

    from services.score_gap_prefetch import prefetch_for_gaps

    prefetch_by_dimension = prefetch_for_gaps(gaps)

    return {
        "dry_run": True,
        "mode": mode,
        "target_date": target,
        "active_stocks_count": active,
        "sync_rate_all_before": gaps.get("sync_rate_all"),
        "sync_rate_required_before": gaps.get("sync_rate_required"),
        "missing_total": gaps.get("missing_total"),
        "planned_actions": planned,
        "total_estimated_ms_range": [total_low, total_high],
        "would_skip": would_skip,
        "prefetch_probe": prefetch_probe,
        "prefetch_by_dimension": prefetch_by_dimension,
        "gap_targets": {k: sorted(v) for k, v in targets.items() if v},
    }
