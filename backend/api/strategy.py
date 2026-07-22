from config import DB_PATH
"""策略研究引擎 — 因子IC + 滚动回测 + 止损止盈 + 行业轮动"""
import sqlite3, math
from datetime import date, timedelta
from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/strategy", tags=["strategy"])
from config import DB_PATH


@router.get("/list")
async def list_strategies(portfolio_only: bool = Query(False)):
    """统一策略列表 — 回测 / 模拟盘下拉共用。"""
    from services.strategy_registry import list_strategies

    return {"strategies": list_strategies(portfolio_only=portfolio_only)}


@router.get("/factor-ic")
def factor_ic_analysis(
    period: int = Query(60, description="IC 序列最多保留期数"),
    forward_days: int = Query(20, description="未来收益天数"),
):
    """因子有效性(IC) — 因子分 vs 未来 N 日股票收益（按数据日期缓存，避免每次45s全算）"""
    from services.factor_analysis_cache import cached_by_date
    from services.ic_engine import analyze_all_score_factors

    return cached_by_date(
        f"ic:all:{period}:{forward_days}",
        lambda: analyze_all_score_factors(forward_days=forward_days, period=period),
        allow_inprocess=False,
    )


def _pearson(x, y):
    n = len(x)
    if n < 2: return None
    mx = sum(x)/n; my = sum(y)/n
    sx = math.sqrt(sum((v-mx)**2 for v in x)/(n-1))
    sy = math.sqrt(sum((v-my)**2 for v in y)/(n-1))
    if sx == 0 or sy == 0: return 0
    cov = sum((x[i]-mx)*(y[i]-my) for i in range(n))/(n-1)
    return round(cov/(sx*sy), 4)


@router.get("/factor-ic/heatmap")
def factor_ic_heatmap(period: int = Query(60)):
    from services.beta_health import attach_meta
    from services.factor_analysis_cache import cached_by_date
    from services.ic_engine import analyze_ic_heatmap

    return attach_meta(
        cached_by_date(f"ic:heatmap:{period}", lambda: analyze_ic_heatmap(period=period), allow_inprocess=False)
    )


@router.get("/rolling-backtest")
async def rolling_backtest(
    window: int = Query(60, description="窗口天数"),
    step: int = Query(20, description="步长天数"),
    top_n: int = Query(5, description="持仓数"),
    min_score: float = Query(50, description="最低分"),
    strategy: str = Query("composite"),
    stop_loss: float = Query(-0.10, description="止损线"),
    take_profit: float = Query(0.30, description="止盈线"),
):
    """滚动窗口回测 — 委托统一引擎"""
    from services.backtest_engine import run_rolling_backtest

    return run_rolling_backtest(
        window=window,
        step=step,
        top_n=top_n,
        min_score=min_score,
        strategy=strategy,
        stop_loss=stop_loss,
        take_profit=take_profit,
    )


@router.get("/sector-rotation-signals")
async def sector_rotation_signals(
    window_days: int = Query(5, ge=2, le=20),
    force: bool = Query(False, description="跳过行业轮动缓存"),
):
    """行业轮动：近 N 个交易日涨跌幅 + 相对跟踪池强度，含行业内个股明细"""
    from services.sector_rotation import compute_sector_rotation_signals

    return compute_sector_rotation_signals(window_days=window_days, force=force)
