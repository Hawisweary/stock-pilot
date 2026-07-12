"""
AI基本面研究员 - Pydantic 数据模型
定义所有 API 请求和响应的数据结构
"""
import re
from pydantic import BaseModel, Field, field_validator
from typing import Optional

_ALLOWED_MARKETS = {"A", "HK", "US"}
_CODE_RE = re.compile(r"^[A-Za-z0-9.]{1,12}$")


# ===== 股票管理 =====

class StockIn(BaseModel):
    """添加股票请求"""
    code: str = Field(..., description="股票代码，如 600519")
    market: str = Field(default="A", description="市场：A/HK/US")

    @field_validator("market")
    @classmethod
    def market_must_be_known(cls, v: str) -> str:
        v = v.upper().strip()
        if v not in _ALLOWED_MARKETS:
            raise ValueError(f"market 必须是 {sorted(_ALLOWED_MARKETS)}")
        return v

    @field_validator("code")
    @classmethod
    def code_must_be_safe(cls, v: str) -> str:
        v = v.strip()
        if not _CODE_RE.match(v):
            raise ValueError("code 只允许字母、数字和点，长度 1-12")
        return v


class StockOut(BaseModel):
    """股票详情响应"""
    id: int
    code: str
    name: str
    market: str
    sector: str = ""
    industry: str = ""
    list_date: str = ""
    is_active: bool = True
    created_at: str = ""
    updated_at: str = ""


class StockWithScores(StockOut):
    """带最新评分的股票信息"""
    latest_scores: Optional[dict] = None
    latest_indicators: Optional[dict] = None


# ===== 数据抓取 =====

class FetchResult(BaseModel):
    """单股票抓取结果"""
    stock_id: int
    code: str
    name: str
    quotes_count: int = 0
    financials_count: int = 0
    indicators_count: int = 0
    status: str = "success"
    error: str = ""


class FetchAllResult(BaseModel):
    """批量抓取结果"""
    results: list[FetchResult]
    total_duration_ms: int = 0


class DataStatus(BaseModel):
    """数据新鲜度状态"""
    stock_id: int
    code: str
    name: str
    last_quote_date: str = ""
    last_report_date: str = ""
    status: str = "fresh"  # fresh / stale / missing


class FetchLogOut(BaseModel):
    """抓取日志"""
    id: int
    stock_id: Optional[int]
    data_type: str
    fetch_time: str
    status: str
    records_count: int
    error_message: str = ""
    duration_ms: int = 0


# ===== 财务数据 =====

class FinancialReportOut(BaseModel):
    """财务报表响应"""
    id: int
    stock_id: int
    report_date: str
    period_end_date: str
    report_type: str
    revenue: Optional[float] = None
    operating_revenue: Optional[float] = None
    net_profit: Optional[float] = None
    net_profit_parent: Optional[float] = None
    gross_profit: Optional[float] = None
    operating_profit: Optional[float] = None
    total_operating_cost: Optional[float] = None
    total_assets: Optional[float] = None
    total_equity: Optional[float] = None
    total_liabilities: Optional[float] = None
    current_assets: Optional[float] = None
    current_liabilities: Optional[float] = None
    operating_cf: Optional[float] = None
    investing_cf: Optional[float] = None
    financing_cf: Optional[float] = None
    eps: Optional[float] = None
    bvps: Optional[float] = None


class DailyQuoteOut(BaseModel):
    """每日行情响应"""
    id: int
    stock_id: int
    trade_date: str
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[float] = None
    amount: Optional[float] = None
    change_pct: Optional[float] = None
    turnover: Optional[float] = None


class IndicatorOut(BaseModel):
    """财务指标响应"""
    id: int
    stock_id: int
    calc_date: str
    pe_ttm: Optional[float] = None
    pb: Optional[float] = None
    ps_ttm: Optional[float] = None
    pcf_ttm: Optional[float] = None
    roe: Optional[float] = None
    roa: Optional[float] = None
    gross_margin: Optional[float] = None
    net_margin: Optional[float] = None
    debt_to_equity: Optional[float] = None
    current_ratio: Optional[float] = None
    dividend_yield: Optional[float] = None
    market_cap: Optional[float] = None


# ===== 因子评分 =====

class ScoreOut(BaseModel):
    """因子评分响应"""
    id: int
    stock_id: int
    calc_date: str
    profitability_score: Optional[float] = None
    growth_score: Optional[float] = None
    safety_score: Optional[float] = None
    value_score: Optional[float] = None
    composite_score: Optional[float] = None


class ScoreRankingOut(BaseModel):
    """评分排名响应"""
    rank: int
    stock_id: int
    code: str
    name: str
    composite_score: Optional[float] = None
    profitability_score: Optional[float] = None
    growth_score: Optional[float] = None
    safety_score: Optional[float] = None
    value_score: Optional[float] = None


class ScoreRecalculateResult(BaseModel):
    """重算结果"""
    updated: int
    status: str


# ===== Dashboard =====

class DashboardOverview(BaseModel):
    """Dashboard 概览"""
    stock_count: int
    active_stocks: int
    stale_stocks: int
    avg_composite_score: float = 0
    top_3_stocks: list[ScoreRankingOut] = []
    last_update: str = ""


# ===== AI 分析 =====

class AiAnalysisIn(BaseModel):
    """AI 分析请求"""
    force: bool = Field(default=False, description="是否强制重新生成")


class AiAnalysisOut(BaseModel):
    """AI 分析响应"""
    id: int
    stock_id: int
    analysis_type: str
    summary: str = ""
    strengths: list[str] = []
    weaknesses: list[str] = []
    factor_commentary: dict = {}
    valuation_view: str = ""
    overall_rating: str = ""
    generated_at: str = ""


# ===== 通用 =====

class ApiResponse(BaseModel):
    """通用 API 响应"""
    ok: bool = True
    message: str = ""
    data: Optional[dict] = None


class ApiVersion(BaseModel):
    """版本信息"""
    version: str
    title: str
