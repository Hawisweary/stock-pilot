"""模拟交易 API"""
from typing import Literal, Optional

import config
from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field

from services.strategy_registry import is_valid_strategy, normalize_strategy_id

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


class TradeRequest(BaseModel):
    code: str
    action: Literal["buy", "sell"]
    shares: int = Field(ge=100)
    skip_risk: bool = False
    reason: str | None = None


class BuildRequest(BaseModel):
    top_n: int = Field(default=5, ge=1, le=50)
    min_score: float = Field(default=50, ge=0, le=100)
    strategy: str = "composite"
    pos_style: Literal["equal", "weighted"] = "equal"
    combination_id: Optional[int] = Field(default=None, ge=1)
    lookback: int = Field(default=20, ge=5, le=250)
    sector_window: int = Field(default=5, ge=2, le=20)
    per_sector: int = Field(default=2, ge=1, le=5)


class CompareRequest(BaseModel):
    days: int = Field(default=90, ge=5, le=365)
    top_n: int = Field(default=5, ge=1, le=50)
    min_score: float = Field(default=50, ge=0, le=100)
    strategy: str = "composite"
    pos_style: Literal["equal", "weighted"] = "equal"
    rebalance: Literal["daily", "weekly", "monthly"] = "weekly"
    combination_id: Optional[int] = Field(default=None, ge=1)
    lookback: int = Field(default=20, ge=5, le=250)
    sector_window: int = Field(default=5, ge=2, le=20)
    per_sector: int = Field(default=2, ge=1, le=5)


class SettingsRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    rebalance_schedule: Optional[Literal["none", "weekly", "monthly"]] = None
    max_weight_pct: Optional[float] = Field(default=None, ge=5, le=100)
    min_cash_pct: Optional[float] = Field(default=None, ge=0, le=50)
    default_strategy: Optional[str] = None
    default_top_n: Optional[int] = Field(default=None, ge=1, le=50)
    default_min_score: Optional[float] = Field(default=None, ge=0, le=100)
    default_pos_style: Optional[Literal["equal", "weighted"]] = None
    default_combination_id: Optional[int] = Field(default=None, ge=1)
    default_lookback: Optional[int] = Field(default=None, ge=5, le=250)
    default_sector_window: Optional[int] = Field(default=None, ge=2, le=20)
    default_per_sector: Optional[int] = Field(default=None, ge=1, le=5)


class RenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)


def _raise_if_error(result: dict) -> dict:
    if "error" in result:
        msg = result["error"]
        code = 404 if "不存在" in msg else 400
        raise HTTPException(status_code=code, detail=msg)
    return result


@router.get("/pricing-context")
async def get_pricing_context():
    from services.trade_pricing import pricing_context_dict

    return pricing_context_dict()


@router.get("/quote-preview")
async def quote_preview(code: str, action: str = "buy"):
    from services.portfolio_analytics import estimate_trade_fees
    from services.trading_rules import validate_shares

    if action not in ("buy", "sell"):
        raise HTTPException(status_code=400, detail="action 须为 buy 或 sell")
    return estimate_trade_fees(code, action, 100)


@router.get("/portfolios")
async def list_portfolios(x_portfolio_owner: Optional[str] = Header(default=None)):
    from services.portfolio_svc import get_portfolios

    rows = get_portfolios()
    if x_portfolio_owner:
        rows = [r for r in rows if (r.get("owner_id") or "default") == x_portfolio_owner]
    return rows


@router.get("/pnl-summary")
async def pnl_summary():
    """所有组合盈亏摘要：去重持仓总览 + 各策略累计/今日盈亏。"""
    from services.portfolio_svc import get_pnl_summary

    data = get_pnl_summary()
    deduped = data.get("deduped") or {}
    return {
        "portfolios": data.get("portfolios") or [],
        "summary": {
            "total_cost": deduped.get("total_cost", 0),
            "total_market_value": deduped.get("total_market_value", 0),
            "total_pnl": deduped.get("total_pnl", 0),
            "total_pnl_pct": deduped.get("total_pnl_pct", 0),
            "market_pnl_pct": deduped.get("market_pnl_pct", 0),
            "today_pnl_pct": deduped.get("today_pnl_pct", 0),
            "display_pnl_pct": deduped.get("display_pnl_pct", 0),
            "is_friction_only": deduped.get("is_friction_only", False),
            "stock_count": deduped.get("stock_count", 0),
            "position_count": deduped.get("position_count", 0),
        },
        "strategy_aggregate": data.get("strategy_aggregate"),
        "meta": data.get("meta"),
    }


@router.post("/portfolios/snapshot-all")
async def snapshot_all():
    from services.portfolio_svc import snapshot_all_portfolios

    return snapshot_all_portfolios()


@router.post("/portfolios/rebalance-due")
async def rebalance_due():
    from services.portfolio_svc import queue_scheduled_rebalances

    return queue_scheduled_rebalances()


@router.post("/portfolios/execute-pending")
async def execute_pending(force: bool = False):
    """手动触发：执行待处理的次日开盘卖单/调仓。force=true 时绕过非交易日/开盘前守卫立即补跑。"""
    from services.portfolio_svc import execute_pending_orders_at_open

    return execute_pending_orders_at_open(force=force)


@router.post("/portfolios/turtle-exits")
async def turtle_exits_all():
    """检查所有海龟策略组合的日频止损/出场（收盘入队，次日开盘执行）。"""
    from services.portfolio_svc import queue_turtle_exits

    return queue_turtle_exits()


@router.get("/portfolios/{portfolio_id}/turtle-exits-preview")
async def turtle_exits_preview(portfolio_id: int):
    """检查海龟出场信号（不卖出）。"""
    from services.portfolio_svc import preview_turtle_exits

    return _raise_if_error(preview_turtle_exits(portfolio_id))


@router.post("/portfolios/{portfolio_id}/turtle-exits")
async def turtle_exits_one(portfolio_id: int):
    from services.portfolio_svc import queue_turtle_exits

    return queue_turtle_exits(portfolio_id=portfolio_id)


@router.post("/portfolios/{portfolio_id}/sync-turtle")
async def sync_turtle(
    portfolio_id: int,
    lookback: int = Query(default=20, ge=10, le=60),
    top_n: int = Query(default=5, ge=1, le=50),
    min_score: float = Query(default=60.0, ge=0, le=100),
    rebalance_schedule: Literal["none", "weekly", "monthly"] = Query(default="none"),
):
    """将海龟策略预设写入组合默认参数。"""
    from services.portfolio_svc import sync_turtle_settings

    return _raise_if_error(
        sync_turtle_settings(
            portfolio_id,
            lookback=lookback,
            top_n=top_n,
            min_score=min_score,
            rebalance_schedule=rebalance_schedule,
        )
    )


@router.post("/portfolios")
async def create_portfolio(
    name: str = "默认组合",
    cash: float = 100000,
    x_portfolio_owner: Optional[str] = Header(default=None),
):
    from services.portfolio_svc import create_portfolio as svc_create

    if cash <= 0:
        raise HTTPException(status_code=400, detail="初始资金须大于 0")
    return svc_create(name, cash, owner_id=x_portfolio_owner or "default")


@router.get("/portfolios/{portfolio_id}")
async def view_portfolio(portfolio_id: int):
    from services.portfolio_svc import get_portfolio

    return _raise_if_error(get_portfolio(portfolio_id))


@router.get("/portfolios/{portfolio_id}/nav-series")
async def portfolio_nav_series(portfolio_id: int, days: int = 90):
    """B-2：组合历史净值曲线（归一化 100 起点）+ CSI300 基准对照。
    Response: { dates[], nav[], benchmark[], base_value }
    """
    import sqlite3
    from datetime import date, timedelta

    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        cutoff = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
        # 组合快照（portfolio_snapshots 按日记录 total_value）
        snaps = conn.execute(
            """SELECT snapshot_date, total_value FROM portfolio_snapshots
               WHERE portfolio_id = ? AND snapshot_date >= ?
               ORDER BY snapshot_date""",
            (portfolio_id, cutoff),
        ).fetchall()
        if not snaps:
            return {"dates": [], "nav": [], "benchmark": [], "base_value": None}

        dates = [r["snapshot_date"] for r in snaps]
        raw_vals = [float(r["total_value"]) for r in snaps]
        base_val = raw_vals[0]
        nav = [round(v / base_val * 100, 2) if base_val > 0 else 100.0 for v in raw_vals]

        # CSI300 基准（sh000300，从 stock_daily_quotes 不存，试 market_index_daily）
        benchmark: list[float] = []
        try:
            idx_rows = conn.execute(
                """SELECT trade_date, close FROM market_index_daily
                   WHERE index_code='sh000300' AND trade_date >= ?
                   AND trade_date IN ({})
                   ORDER BY trade_date""".format(",".join("?" * len(dates))),
                [cutoff] + dates,
            ).fetchall() if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_index_daily'"
            ).fetchone() else []
            if idx_rows and len(idx_rows) >= 2:
                bmap = {r["trade_date"]: float(r["close"]) for r in idx_rows}
                b0 = bmap.get(dates[0])
                if b0 and b0 > 0:
                    benchmark = [round(bmap[d] / b0 * 100, 2) if d in bmap else None for d in dates]  # type: ignore
        except Exception:
            pass

        return {
            "dates": dates,
            "nav": nav,
            "benchmark": benchmark,
            "base_value": base_val,
        }
    finally:
        conn.close()


@router.get("/portfolios/{portfolio_id}/metrics")
async def portfolio_metrics(portfolio_id: int):
    from services.portfolio_analytics import compute_metrics

    return _raise_if_error(compute_metrics(portfolio_id))


@router.get("/portfolios/{portfolio_id}/export")
async def export_portfolio(portfolio_id: int):
    from services.portfolio_analytics import export_portfolio_csv

    try:
        content = export_portfolio_csv(portfolio_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return Response(
        content=content.encode("utf-8-sig"),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=portfolio_{portfolio_id}.csv"},
    )


@router.get("/portfolios/{portfolio_id}/estimate-fees")
async def estimate_fees(portfolio_id: int, code: str, action: str, shares: int):
    from services.portfolio_analytics import estimate_trade_fees
    from services.trading_rules import validate_shares

    if err := validate_shares(shares):
        raise HTTPException(status_code=400, detail=err)
    return _raise_if_error(estimate_trade_fees(code, action, shares))


@router.delete("/portfolios/{portfolio_id}")
async def remove_portfolio(portfolio_id: int):
    from services.portfolio_svc import delete_portfolio, get_portfolio

    _raise_if_error(get_portfolio(portfolio_id))
    return delete_portfolio(portfolio_id)


@router.patch("/portfolios/{portfolio_id}")
async def patch_portfolio(portfolio_id: int, req: RenameRequest):
    from services.portfolio_svc import rename_portfolio

    return _raise_if_error(rename_portfolio(portfolio_id, req.name))


@router.put("/portfolios/{portfolio_id}/settings")
async def put_settings(portfolio_id: int, req: SettingsRequest):
    from services.portfolio_svc import update_portfolio_settings

    if req.default_strategy and not is_valid_strategy(
        req.default_strategy,
        combination_id=req.default_combination_id,
    ):
        raise HTTPException(status_code=400, detail=f"未知策略: {req.default_strategy}")
    payload = req.model_dump(exclude_none=True)
    return _raise_if_error(update_portfolio_settings(portfolio_id, **payload))


@router.post("/portfolios/{portfolio_id}/trade")
async def do_trade(portfolio_id: int, req: TradeRequest):
    from services.portfolio_svc import trade
    from services.trading_rules import validate_shares

    if err := validate_shares(req.shares):
        raise HTTPException(status_code=400, detail=err)
    if req.skip_risk and not (req.reason or "").strip():
        raise HTTPException(status_code=400, detail="skip_risk=true 必须提供 reason（说明跳过风控原因）")
    return _raise_if_error(trade(portfolio_id, req.code, req.action, req.shares, skip_risk=req.skip_risk, reason=req.reason))


def _build_top(portfolio_id: int, req: BuildRequest) -> dict:
    if not is_valid_strategy(req.strategy, combination_id=req.combination_id):
        raise HTTPException(status_code=400, detail=f"未知策略: {req.strategy}")
    from services.portfolio_svc import queue_build_top_n

    return _raise_if_error(
        queue_build_top_n(
            portfolio_id,
            top_n=req.top_n,
            min_score=req.min_score,
            strategy=normalize_strategy_id(req.strategy),
            pos_style=req.pos_style,
            combination_id=req.combination_id,
            lookback=req.lookback,
            sector_window=req.sector_window,
            per_sector=req.per_sector,
        )
    )


@router.post("/portfolios/{portfolio_id}/sync-sector-rotation")
async def sync_sector_rotation(
    portfolio_id: int,
    window_days: Optional[int] = Query(default=None, ge=2, le=20),
    per_sector: int = Query(default=2, ge=1, le=5),
    top_n: int = Query(default=10, ge=1, le=50),
    min_score: float = Query(default=0.0, ge=0, le=100),
):
    """将行业轮动信号写入组合默认策略（sector_rotation + 窗口参数）。"""
    from services.portfolio_svc import sync_sector_rotation_settings

    return _raise_if_error(
        sync_sector_rotation_settings(
            portfolio_id,
            window_days=window_days,
            per_sector=per_sector,
            top_n=top_n,
            min_score=min_score,
        )
    )


@router.post("/portfolios/{portfolio_id}/build-preview")
async def build_preview(portfolio_id: int, req: BuildRequest):
    from services.portfolio_analytics import preview_build_top

    return _raise_if_error(
        preview_build_top(
            portfolio_id,
            top_n=req.top_n,
            min_score=req.min_score,
            strategy=req.strategy,
            pos_style=req.pos_style,
            combination_id=req.combination_id,
            lookback=req.lookback,
            sector_window=req.sector_window,
            per_sector=req.per_sector,
        )
    )


@router.post("/portfolios/{portfolio_id}/compare-backtest")
async def compare_backtest(portfolio_id: int, req: CompareRequest):
    from services.portfolio_analytics import compare_with_backtest

    return _raise_if_error(
        compare_with_backtest(
            portfolio_id,
            days=req.days,
            top_n=req.top_n,
            min_score=req.min_score,
            strategy=req.strategy,
            pos_style=req.pos_style,
            rebalance=req.rebalance,
        )
    )


@router.post("/portfolios/{portfolio_id}/build-top")
async def build_top_n(portfolio_id: int, req: BuildRequest):
    return _build_top(portfolio_id, req)


@router.post("/portfolios/{portfolio_id}/import-holdings")
async def import_holdings(portfolio_id: int, req: BuildRequest):
    return _build_top(portfolio_id, req)


@router.get("/portfolios/{portfolio_id}/rebalance-preview")
async def get_rebalance_preview(portfolio_id: int):
    """预告下次自动调仓的买卖计划（不执行交易）"""
    from services.portfolio_svc import rebalance_preview
    return _raise_if_error(rebalance_preview(portfolio_id))


@router.post("/portfolios/{portfolio_id}/skip-rebalance")
async def skip_rebalance(portfolio_id: int, skip: bool = True):
    """设置/取消跳过下次自动调仓"""
    from services.portfolio_svc import set_skip_next_rebalance
    return _raise_if_error(set_skip_next_rebalance(portfolio_id, skip))


@router.get("/portfolios/{portfolio_id}/score-alerts")
async def get_score_alerts(portfolio_id: int, threshold: float = 40.0):
    """触发式调仓：返回评分低于阈值或被强否决的持仓。"""
    from services.portfolio_svc import score_alerts
    return _raise_if_error(score_alerts(portfolio_id, threshold=threshold))


class ReplaceRequest(BaseModel):
    sell_code: str
    strategy: str = "composite"
    min_score: float = Field(default=50.0, ge=0, le=100)
    combination_id: Optional[int] = Field(default=None, ge=1)
    lookback: int = Field(default=20, ge=5, le=250)
    sector_window: int = Field(default=5, ge=2, le=20)
    per_sector: int = Field(default=2, ge=1, le=5)


@router.post("/portfolios/{portfolio_id}/replace-position")
async def replace_position(portfolio_id: int, req: ReplaceRequest):
    """触发式调仓：卖出指定股，买入最高分候选股。"""
    from services.portfolio_svc import replace_position as svc_replace
    return _raise_if_error(
        svc_replace(
            portfolio_id,
            sell_code=req.sell_code,
            strategy=req.strategy,
            min_score=req.min_score,
            combination_id=req.combination_id,
            lookback=req.lookback,
            sector_window=req.sector_window,
            per_sector=req.per_sector,
        )
    )
