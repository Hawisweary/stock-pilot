"""回测系统 API — 统一引擎 + 参数扫描 + 历史"""
import json

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


class SaveRunRequest(BaseModel):
    params: dict
    result: dict


@router.get("/run")
async def run_backtest_api(
    days: int = Query(90, description="回测交易日数"),
    top_n: int = Query(5, description="持仓数量"),
    lookback: int = Query(20, description="因子/动量窗口"),
    pos_style: str = Query("equal", description="equal|weighted"),
    strategy: str = Query("composite", description="composite|fundamental|...|momentum|factor_combination|F0xx"),
    min_score: float = Query(50, description="最低分"),
    rebalance: str = Query("weekly", description="daily|weekly|monthly"),
    apply_costs: bool = Query(True, description="含交易成本"),
    apply_t1: bool = Query(True, description="T+1"),
    combination_id: int | None = Query(None, description="合成方案 ID（strategy=factor_combination）"),
    save: bool = Query(False, description="保存到历史"),
    engine: str = Query("python", description="python|rust"),
    ml_horizon: int | None = Query(None, description="ml_pred 策略预测周期 5/20/60"),
    benchmark_mode: str = Query("equal", description="equal|csi300"),
    sector_window: int = Query(5, ge=2, le=20, description="行业轮动回看交易日"),
    exec_mode: str = Query("next_open", description="next_open（T+1 开盘，默认）|close（legacy 当日收盘）"),
    initial_cash: float = Query(100_000, description="初始资金（元）"),
    commission: float | None = Query(None, description="覆盖佣金率（如 0.00025）"),
    slippage: float | None = Query(None, description="覆盖滑点（如 0.001）"),
    stamp_tax: float | None = Query(None, description="覆盖印花税（如 0.0005）"),
    risk_free_rate: float = Query(0.02, description="年化无风险利率，Sharpe 用"),
):
    from services.backtest_engine import save_backtest_run
    from services.backtest_rust_adapter import run_backtest_with_engine

    params = {
        "days": days,
        "top_n": top_n,
        "lookback": lookback,
        "pos_style": pos_style,
        "strategy": strategy,
        "min_score": min_score,
        "rebalance": rebalance,
        "apply_costs": apply_costs,
        "apply_t1": apply_t1,
        "combination_id": combination_id,
        "ml_horizon": ml_horizon,
        "benchmark_mode": benchmark_mode,
        "sector_window": sector_window,
        "exec_mode": exec_mode,
        "initial_cash": initial_cash,
        "commission_override": commission,
        "slippage_override": slippage,
        "stamp_tax_override": stamp_tax,
        "risk_free_rate": risk_free_rate,
    }
    result = run_backtest_with_engine(params, engine=engine)
    if save and "error" not in result:
        slim = {k: result[k] for k in result if k not in ("daily_values", "drawdowns", "benchmark_curve") if k in result}
        save_backtest_run(params, slim)
    return result


@router.get("/scan")
async def parameter_scan(
    days: int = Query(90),
    strategy: str = Query("composite"),
):
    from services.backtest_engine import run_parameter_scan

    return run_parameter_scan(days=days, strategy=strategy)


@router.get("/rolling")
async def rolling_backtest_api(
    window: int = Query(60),
    step: int = Query(20),
    top_n: int = Query(5),
    min_score: float = Query(50),
    strategy: str = Query("composite"),
):
    from services.backtest_engine import run_rolling_backtest

    return run_rolling_backtest(
        window=window, step=step, top_n=top_n, min_score=min_score, strategy=strategy
    )


@router.get("/walk-forward")
async def walk_forward_api(
    train_days: int = Query(60),
    test_days: int = Query(20),
    top_n: int = Query(5),
    strategy: str = Query("composite"),
):
    from services.backtest_engine import run_walk_forward

    return run_walk_forward(train_days=train_days, test_days=test_days, top_n=top_n, strategy=strategy)


@router.get("/history")
async def backtest_history(limit: int = Query(10)):
    from services.backtest_engine import list_backtest_runs

    return list_backtest_runs(limit=limit)


@router.post("/history")
async def save_backtest_history(req: SaveRunRequest):
    from services.backtest_engine import save_backtest_run

    return save_backtest_run(req.params, req.result)


@router.get("/legacy")
async def run_legacy_backtest(
    days: int = Query(60),
    top_n: int = Query(5),
    min_score: int = Query(60),
    pos_style: str = Query("equal"),
):
    from services.backtest_engine import run_backtest

    return run_backtest(
        days=days,
        top_n=top_n,
        min_score=min_score,
        pos_style=pos_style,
        strategy="composite",
        rebalance="daily",
    )
