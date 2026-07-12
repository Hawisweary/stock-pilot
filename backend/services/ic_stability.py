"""IC 稳定性评估 — 阶段 III 因子合成前置检查"""
from __future__ import annotations

from typing import List

from services.ic_engine import analyze_factor_id

# 技术面因子（factor_history_expand 已回填 ≥60 天）
DEFAULT_REVIEW_FACTORS: List[str] = ["F010", "F011", "F012", "F013"]
MIN_IC_PERIODS = 20
MIN_STABLE_FACTORS = 2
MIN_ABS_IR = 0.15


def review_factor_ic(
    factor_ids: List[str] | None = None,
    forward_days: int = 20,
    max_dates: int = 60,
) -> dict:
    factor_ids = factor_ids or DEFAULT_REVIEW_FACTORS
    reviews = []
    stable_count = 0
    for fid in factor_ids:
        r = analyze_factor_id(fid, forward_days=forward_days, max_dates=max_dates)
        n = r.get("n_periods") or 0
        ir = abs(r.get("ir") or 0)
        stable = n >= MIN_IC_PERIODS and ir >= MIN_ABS_IR
        if stable:
            stable_count += 1
        reviews.append(
            {
                "factor_id": fid,
                "n_periods": n,
                "mean_ic": r.get("mean_ic"),
                "ir": r.get("ir"),
                "ic_positive_ratio": r.get("ic_positive_ratio"),
                "stable": stable,
                "error": r.get("error"),
            }
        )
    ready = stable_count >= MIN_STABLE_FACTORS
    return {
        "factors": reviews,
        "stable_count": stable_count,
        "min_stable_required": MIN_STABLE_FACTORS,
        "ic_stable_ready": ready,
        "forward_days": forward_days,
        "max_dates": max_dates,
    }


def is_ic_stable() -> bool:
    return review_factor_ic()["ic_stable_ready"]
