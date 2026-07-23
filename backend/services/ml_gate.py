"""ML 预测消费门控：环境变量强制 + OOS RankIC 指标门控（Day 5）。"""
from __future__ import annotations

import os
import statistics
from typing import Any

from config import (
    DB_PATH,
    ML_DEFAULT_HORIZON,
    ML_GATE_MAX_RANK_IC_STD,
    ML_GATE_MIN_FOLDS,
    ML_GATE_MIN_MEAN_RANK_IC,
    ML_GATE_RECENT_FOLDS,
    QLIB_PREDICTIONS_APPROVED_FORCE,
)
from services.ml_train_store import get_latest_train_runs


def _force_override() -> bool | None:
    """AFR_QLIB_PREDICTIONS_APPROVED：未设置则 None；true/false 强制覆盖指标门控。"""
    return QLIB_PREDICTIONS_APPROVED_FORCE


def evaluate_metric_gate(
    db_path: str | None = None,
    *,
    horizon: int | None = None,
) -> dict[str, Any]:
    path = db_path or DB_PATH
    h = horizon if horizon is not None else ML_DEFAULT_HORIZON
    runs = get_latest_train_runs(path, horizon=h, limit=ML_GATE_RECENT_FOLDS)
    ics = [float(r["oos_rank_ic"]) for r in runs if r.get("oos_rank_ic") is not None]
    n = len(ics)
    mean_ic = sum(ics) / n if n else None
    std_ic = statistics.stdev(ics) if n >= 2 else None

    reasons: list[str] = []
    passed = True

    if n < ML_GATE_MIN_FOLDS:
        passed = False
        reasons.append(f"folds_with_rank_ic={n} < min_folds={ML_GATE_MIN_FOLDS}")
    if mean_ic is None:
        passed = False
        reasons.append("no_oos_rank_ic")
    elif mean_ic < ML_GATE_MIN_MEAN_RANK_IC:
        passed = False
        reasons.append(
            f"mean_rank_ic={mean_ic:.4f} < threshold={ML_GATE_MIN_MEAN_RANK_IC}"
        )
    if (
        passed
        and ML_GATE_MAX_RANK_IC_STD is not None
        and std_ic is not None
        and std_ic > ML_GATE_MAX_RANK_IC_STD
    ):
        passed = False
        reasons.append(
            f"rank_ic_std={std_ic:.4f} > max_std={ML_GATE_MAX_RANK_IC_STD}"
        )

    return {
        "horizon": h,
        "gate_passed": passed,
        "recent_folds_requested": ML_GATE_RECENT_FOLDS,
        "folds_with_rank_ic": n,
        "min_folds_required": ML_GATE_MIN_FOLDS,
        "mean_rank_ic": round(mean_ic, 4) if mean_ic is not None else None,
        "rank_ic_std": round(std_ic, 4) if std_ic is not None else None,
        "min_mean_rank_ic": ML_GATE_MIN_MEAN_RANK_IC,
        "max_rank_ic_std": ML_GATE_MAX_RANK_IC_STD,
        "failure_reasons": reasons,
        "recent_rank_ic": [round(x, 4) for x in ics],
    }


def is_ml_predictions_approved(
    db_path: str | None = None,
    *,
    horizon: int | None = None,
) -> bool:
    forced = _force_override()
    if forced is True:
        return True
    if forced is False:
        return False
    return evaluate_metric_gate(db_path, horizon=horizon)["gate_passed"]


def ml_predictions_gate_status(
    db_path: str | None = None,
    *,
    horizon: int | None = None,
) -> dict[str, Any]:
    forced = _force_override()
    gate = evaluate_metric_gate(db_path, horizon=horizon)
    approved = is_ml_predictions_approved(db_path, horizon=horizon)
    mode = "forced_on" if forced is True else "forced_off" if forced is False else "metric_gate"
    return {
        "approved": approved,
        "mode": mode,
        "metric_gate": gate,
    }
