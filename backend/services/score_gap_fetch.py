"""gap 分维度实际 prefetch 拉取（写 DB）"""
from __future__ import annotations

from typing import Any

import config
from services.score_gap_scanner import scan_gaps


def fetch_sentiment_for_gaps(
    gap_report: dict | None = None,
    *,
    stock_ids: list[int] | None = None,
    include_stale: bool = True,
) -> dict[str, Any]:
    """对 sentiment no_source / stale / missing 股票拉新闻并评分。"""
    gaps = gap_report or scan_gaps(stock_ids=stock_ids)
    target_ids: set[int] = set()
    for g in gaps.get("gaps", []):
        if g.get("dimension") != "sentiment_score":
            continue
        status = g.get("status")
        if status in ("no_source", "missing") or (include_stale and status == "stale"):
            target_ids.add(int(g["stock_id"]))

    if stock_ids:
        id_filter = set(stock_ids)
        target_ids &= id_filter

    if not target_ids:
        return {
            "dimension": "sentiment_score",
            "attempted": 0,
            "skipped": True,
            "reason": "no sentiment gaps",
            "target_date": gaps.get("target_date"),
        }

    from services.news_fetcher import fetch_and_analyze_sentiment_batch

    result = fetch_and_analyze_sentiment_batch(sorted(target_ids))
    return {
        "dimension": "sentiment_score",
        "target_date": gaps.get("target_date"),
        "gap_stock_count": len(target_ids),
        **result,
    }


def fetch_sources_for_gaps(
    gap_report: dict | None = None,
    *,
    dimensions: list[str] | None = None,
    stock_ids: list[int] | None = None,
) -> dict[str, dict]:
    """按维度批量拉源数据（当前实现 sentiment）。"""
    gaps = gap_report or scan_gaps(stock_ids=stock_ids)
    dims = dimensions or ["sentiment_score"]
    out: dict[str, dict] = {}
    if "sentiment_score" in dims:
        out["sentiment_score"] = fetch_sentiment_for_gaps(gaps, stock_ids=stock_ids)
    return out
