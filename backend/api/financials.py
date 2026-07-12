from config import DB_PATH

"""
财务数据 API
- GET /api/stocks/{id}/financials?years=5     年报数据
- GET /api/stocks/{id}/indicators?days=365    历史财务指标
- GET /api/stocks/{id}/quotes?days=365        每日行情
"""
from fastapi import APIRouter, HTTPException
from api_utils import execute_sql

router = APIRouter(prefix="/api", tags=["financials"])


@router.get("/stocks/{stock_id}/financials")
async def get_financials(stock_id: int, years: int = 5, report_type: str = "annual"):
    """获取股票财务报表"""
    rows = execute_sql(
        """SELECT * FROM financial_reports
           WHERE stock_id=? AND report_type=?
           ORDER BY period_end_date DESC
           LIMIT ?""",
        (stock_id, report_type, years)
    )
    return rows


@router.get("/stocks/{stock_id}/indicators")
async def get_indicators(stock_id: int, days: int = 365):
    """获取历史财务指标"""
    rows = execute_sql(
        """SELECT * FROM financial_indicators
           WHERE stock_id=?
           ORDER BY calc_date DESC
           LIMIT ?""",
        (stock_id, days)
    )
    return rows


@router.get("/stocks/{stock_id}/valuation")
async def get_valuation(stock_id: int, limit: int = 30):
    """获取估值快照（PE/PB/市值，与财报指标分离）"""
    rows = execute_sql(
        """SELECT as_of_date, pe_ttm, pb, market_cap, dividend_yield, source
           FROM valuation_snapshots
           WHERE stock_id=?
           ORDER BY as_of_date DESC
           LIMIT ?""",
        (stock_id, limit),
    )
    latest = rows[0] if rows else None
    return {"latest": latest, "history": rows}


@router.get("/stocks/{stock_id}/quotes")
async def get_quotes(stock_id: int, days: int = 365):
    """获取每日行情数据"""
    rows = execute_sql(
        """SELECT * FROM stock_daily_quotes
           WHERE stock_id=?
           ORDER BY trade_date DESC
           LIMIT ?""",
        (stock_id, days)
    )
    return rows
