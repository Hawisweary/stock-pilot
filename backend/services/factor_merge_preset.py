"""预设因子合成 — 阶段 III 默认技术面多因子组合"""
from __future__ import annotations

from typing import List

from services.factor_merge import merge_factors_equal, merge_factors_ic_ir, merge_factors_rolling_optimal
from services.ic_stability import review_factor_ic

# 低波 + 量价 + RSI + 趋势（IC 审查通过的技术面因子）
TECH_MERGE_INPUTS: List[str] = ["F010", "F011", "F012", "F013"]

PRESETS = [
    {"key": "tech_equal", "name": "tech_multi_equal", "method": "equal", "factor_ids": TECH_MERGE_INPUTS},
    {"key": "tech_ic_ir", "name": "tech_multi_ic_ir", "method": "ic_ir", "factor_ids": TECH_MERGE_INPUTS},
    {"key": "tech_rolling", "name": "tech_multi_rolling", "method": "rolling_optimal", "factor_ids": TECH_MERGE_INPUTS},
]


def run_preset_merges(skip_ic_check: bool = False) -> dict:
    from config import FACTOR_MERGE_ENABLED

    if not FACTOR_MERGE_ENABLED:
        return {"error": "AFR_FACTOR_MERGE_ENABLED=false", "results": []}

    ic_review = review_factor_ic(TECH_MERGE_INPUTS)
    if not skip_ic_check and not ic_review["ic_stable_ready"]:
        return {
            "error": "ic_not_stable",
            "ic_review": ic_review,
            "results": [],
        }

    results = []
    for preset in PRESETS:
        if preset["method"] == "ic_ir":
            r = merge_factors_ic_ir(preset["factor_ids"], preset["name"])
        elif preset["method"] == "rolling_optimal":
            r = merge_factors_rolling_optimal(preset["factor_ids"], preset["name"])
        else:
            r = merge_factors_equal(preset["factor_ids"], preset["name"])
        results.append({"preset": preset["key"], **r})

    ok = [x for x in results if "error" not in x]
    return {
        "ic_review": ic_review,
        "presets_run": len(PRESETS),
        "success_count": len(ok),
        "results": results,
    }
