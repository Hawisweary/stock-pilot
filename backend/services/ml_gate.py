"""ML 预测消费门控：环境变量强制 + OOS RankIC 指标门控（Day 5）。"""
from __future__ import annotations

import os
import statistics
from typing import Any

from config import (
    DB_PATH,
    ML_DEFAULT_HORIZON,
    ML_GATE_DRIFT_FOLDS,
    ML_GATE_MAX_DRIFT,
    ML_GATE_MAX_RANK_IC_STD,
    ML_GATE_MIN_FOLDS,
    ML_GATE_MIN_FULL_MEAN_RANK_IC,
    ML_GATE_MIN_MEAN_RANK_IC,
    ML_GATE_RECENT_FOLDS,
    QLIB_PREDICTIONS_APPROVED_FORCE,
)
from services.ml_train_store import get_all_train_runs, get_latest_train_runs


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
    # 门控只评估「当前候选模型」——即该 horizon 最新一折的 model_version。
    # 否则 v1/v2 折混在一起,全历史均值/漂移都算错(v1 长期≈0 会污染 v2 评估，反之亦然)。
    _latest = get_latest_train_runs(path, horizon=h, limit=1)
    active_version = _latest[0].get("model_version") if _latest else None
    runs = get_latest_train_runs(
        path, horizon=h, limit=ML_GATE_RECENT_FOLDS, model_version=active_version
    )
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

    # 全历史稳健性：近窗通过还不够——长期无 edge 或时序漂移严重时一律判否，
    # 防止「近期抽样运气」骗过只看近 N 折的门控。
    all_runs = get_all_train_runs(path, horizon=h, model_version=active_version)
    all_ics = [float(r["oos_rank_ic"]) for r in all_runs if r.get("oos_rank_ic") is not None]
    full_mean_ic = sum(all_ics) / len(all_ics) if all_ics else None
    drift = None
    k = ML_GATE_DRIFT_FOLDS
    if len(all_ics) >= 2 * k:
        early = all_ics[:k]
        recent = all_ics[-k:]
        drift = (sum(recent) / k) - (sum(early) / k)

    if full_mean_ic is not None and full_mean_ic < ML_GATE_MIN_FULL_MEAN_RANK_IC:
        passed = False
        reasons.append(
            f"full_mean_rank_ic={full_mean_ic:.4f} < floor={ML_GATE_MIN_FULL_MEAN_RANK_IC}"
        )
    if (
        ML_GATE_MAX_DRIFT is not None
        and drift is not None
        and abs(drift) > ML_GATE_MAX_DRIFT
    ):
        passed = False
        reasons.append(
            f"fold_drift={drift:+.4f} exceeds max_drift={ML_GATE_MAX_DRIFT}"
        )

    return {
        "horizon": h,
        "model_version": active_version,
        "gate_passed": passed,
        "recent_folds_requested": ML_GATE_RECENT_FOLDS,
        "folds_with_rank_ic": n,
        "min_folds_required": ML_GATE_MIN_FOLDS,
        "mean_rank_ic": round(mean_ic, 4) if mean_ic is not None else None,
        "rank_ic_std": round(std_ic, 4) if std_ic is not None else None,
        "min_mean_rank_ic": ML_GATE_MIN_MEAN_RANK_IC,
        "max_rank_ic_std": ML_GATE_MAX_RANK_IC_STD,
        "total_folds": len(all_ics),
        "full_mean_rank_ic": round(full_mean_ic, 4) if full_mean_ic is not None else None,
        "min_full_mean_rank_ic": ML_GATE_MIN_FULL_MEAN_RANK_IC,
        "fold_drift": round(drift, 4) if drift is not None else None,
        "max_drift": ML_GATE_MAX_DRIFT,
        "drift_folds": k,
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
