"""Qlib / ML 实验 API"""
from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api/qlib", tags=["qlib"])


class TrainRequest(BaseModel):
    train_days: int = 120
    test_days: int = 20
    model: str = "lightgbm"
    forward_days: int | None = None
    horizons: list[int] | None = None


@router.get("/predictions")
async def list_predictions(
    limit: int = Query(10),
    horizon: int | None = Query(None, description="预测周期交易日，默认 20"),
):
    from config import ML_DEFAULT_HORIZON, ML_HORIZONS, QLIB_ENABLED, QLIB_PREDICTIONS_APPROVED
    from services.ml_predictions import get_latest_predictions, list_ml_horizons

    if not QLIB_ENABLED and not QLIB_PREDICTIONS_APPROVED:
        return {"enabled": False, "predictions": [], "note": "AFR_QLIB_ENABLED=false"}
    h = horizon if horizon is not None else ML_DEFAULT_HORIZON
    return {
        "enabled": True,
        "horizon": h,
        "horizons_available": list(ML_HORIZONS),
        "horizon_status": list_ml_horizons(),
        "predictions": get_latest_predictions(limit=limit, horizon=h),
    }


@router.post("/train")
async def train_qlib(req: TrainRequest = TrainRequest()):
    from config import QLIB_ENABLED
    from services.job_queue import enqueue

    if not QLIB_ENABLED:
        return {"error": "AFR_QLIB_ENABLED=false", "status": "rejected"}
    job = enqueue("qlib_train", req.model_dump())
    return {"job_id": job.id, "status": job.status.value}


@router.post("/seed-demo")
async def seed_demo_predictions():
    """演示：用 composite 写入 ml_predictions（开发/测试）"""
    from services.ml_predictions import seed_demo_predictions

    n = seed_demo_predictions()
    return {"predictions_written": n, "note": "demo composite seed; set AFR_QLIB_PREDICTIONS_APPROVED=true to use in backtest"}
