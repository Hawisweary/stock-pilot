"""
多维股票筛选器 API (PC-1.0 P0-1a)

主表：v_stock_scores（已含10维度分）
支持条件：综合分、各维度分、行业、veto状态、市场
"""
from __future__ import annotations

import sqlite3
import json
from typing import Optional
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
import config

router = APIRouter(prefix="/api/screener", tags=["screener"])


def _db():
    return sqlite3.connect(config.DB_PATH)


# ── 条件模型 ──────────────────────────────────────────────
class RangeFilter(BaseModel):
    min: Optional[float] = None
    max: Optional[float] = None


class ScreenerQuery(BaseModel):
    score: Optional[RangeFilter] = None
    fundamental_score: Optional[RangeFilter] = None
    technical_score: Optional[RangeFilter] = None
    sentiment_score: Optional[RangeFilter] = None
    capital_score: Optional[RangeFilter] = None
    policy_score: Optional[RangeFilter] = None
    mood_score: Optional[RangeFilter] = None
    val_score: Optional[RangeFilter] = None
    quality_score: Optional[RangeFilter] = None
    industry_score: Optional[RangeFilter] = None
    market_env_score: Optional[RangeFilter] = None

    industries: Optional[list[str]] = Field(None, description="行业列表（OR）")
    markets: Optional[list[str]] = Field(None, description="市场列表，如 ['SH','SZ']")
    veto_status: Optional[list[str]] = Field(None, description="['ok','reduce','exclude']")
    exclude_veto: bool = Field(False, description="排除所有 veto != ok")

    sort_by: str = Field("score", description="排序字段")
    sort_dir: str = Field("desc", pattern="^(asc|desc)$")
    limit: int = Field(100, ge=1, le=5000)
    offset: int = Field(0, ge=0)


SCORE_COLS = {
    "score", "fundamental_score", "technical_score", "sentiment_score",
    "capital_score", "policy_score", "mood_score", "val_score",
    "quality_score", "industry_score", "market_env_score",
}

ALLOWED_SORT = SCORE_COLS | {"code", "name", "calc_date"}


@router.post("/query")
def query_screener(body: ScreenerQuery):
    """
    执行筛选，返回符合条件的股票列表。
    前端 Screener 页面使用。
    """
    conditions: list[str] = []
    params: list = []

    def add_range(col: str, rf: Optional[RangeFilter]):
        if rf is None:
            return
        if rf.min is not None:
            conditions.append(f"{col} >= ?")
            params.append(rf.min)
        if rf.max is not None:
            conditions.append(f"{col} <= ?")
            params.append(rf.max)

    add_range("score", body.score)
    add_range("fundamental_score", body.fundamental_score)
    add_range("technical_score", body.technical_score)
    add_range("sentiment_score", body.sentiment_score)
    add_range("capital_score", body.capital_score)
    add_range("policy_score", body.policy_score)
    add_range("mood_score", body.mood_score)
    add_range("val_score", body.val_score)
    add_range("quality_score", body.quality_score)
    add_range("industry_score", body.industry_score)
    add_range("market_env_score", body.market_env_score)

    if body.industries:
        ph = ",".join("?" * len(body.industries))
        conditions.append(f"(industry IN ({ph}) OR industry_sw IN ({ph}))")
        params.extend(body.industries * 2)

    if body.markets:
        ph = ",".join("?" * len(body.markets))
        conditions.append(f"market IN ({ph})")
        params.extend(body.markets)

    if body.exclude_veto:
        conditions.append("(veto_status IS NULL OR veto_status = 'ok')")
    elif body.veto_status:
        ph = ",".join("?" * len(body.veto_status))
        conditions.append(f"veto_status IN ({ph})")
        params.extend(body.veto_status)

    # 只返回有评分的股票
    conditions.append("score IS NOT NULL")

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    sort_col = body.sort_by if body.sort_by in ALLOWED_SORT else "score"
    sort_dir = "DESC" if body.sort_dir == "desc" else "ASC"

    sql = f"""
        SELECT stock_id, code, name, market, industry, industry_sw,
               calc_date, score,
               fundamental_score, technical_score, sentiment_score,
               capital_score, policy_score, mood_score, val_score,
               quality_score, industry_score, market_env_score,
               veto_status
        FROM v_stock_scores
        {where}
        ORDER BY {sort_col} {sort_dir} NULLS LAST
        LIMIT ? OFFSET ?
    """
    params.extend([body.limit, body.offset])

    conn = _db()
    try:
        count_sql = f"SELECT COUNT(*) FROM v_stock_scores {where}"
        total = conn.execute(count_sql, params[: len(params) - 2]).fetchone()[0]
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    cols = [
        "stock_id", "code", "name", "market", "industry", "industry_sw",
        "calc_date", "score",
        "fundamental_score", "technical_score", "sentiment_score",
        "capital_score", "policy_score", "mood_score", "val_score",
        "quality_score", "industry_score", "market_env_score",
        "veto_status",
    ]
    return {
        "total": total,
        "limit": body.limit,
        "offset": body.offset,
        "results": [dict(zip(cols, r)) for r in rows],
    }


@router.get("/industries")
def get_industries():
    """返回库内所有行业列表（用于筛选器下拉）。"""
    conn = _db()
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT industry FROM stocks WHERE industry IS NOT NULL AND is_active=1
            UNION
            SELECT DISTINCT industry_sw FROM stocks WHERE industry_sw IS NOT NULL AND is_active=1
            ORDER BY 1
            """
        ).fetchall()
    finally:
        conn.close()
    return {"industries": [r[0] for r in rows if r[0]]}


@router.get("/presets")
def get_presets():
    """内置预设筛选条件。"""
    return {
        "presets": [
            # ── 价值锚（composite_v5 主视角）──────────────────────────────
            {
                "id": "value",
                "label": "🏦 价值/持有",
                "desc": "综合分 ≥ 65，低估值高质量，适合长持（银行/消费/白马）",
                "profile": "value",
                "query": {"score": {"min": 65}, "val_score": {"min": 50}, "quality_score": {"min": 50}, "exclude_veto": True, "sort_by": "score"},
            },
            # ── 动量视角（技术+资金+行业轮动，预期差）────────────────────
            {
                "id": "momentum",
                "label": "🚀 动量/轮动",
                "desc": "技术分 ≥ 60 且 资金分 ≥ 60，适合追涨（科技/赛道/主题）",
                "profile": "momentum",
                "query": {"technical_score": {"min": 60}, "capital_score": {"min": 60}, "sort_by": "technical_score"},
            },
            # ── 红利视角（股息+质量，防御）────────────────────────────────
            {
                "id": "dividend",
                "label": "💰 红利/防御",
                "desc": "质量分 ≥ 60 且 估值分 ≥ 55，股息稳健（银行/电力/公用事业）",
                "profile": "dividend",
                "query": {"quality_score": {"min": 60}, "val_score": {"min": 55}, "exclude_veto": True, "sort_by": "quality_score"},
            },
            # ── 原有预设保留 ───────────────────────────────────────────────
            {
                "id": "high_score",
                "label": "高综合分",
                "desc": "综合分 ≥ 65，无否决",
                "query": {"score": {"min": 65}, "exclude_veto": True, "sort_by": "score"},
            },
            {
                "id": "undervalued",
                "label": "低估值高质量",
                "desc": "估值分 ≥ 55 且 质量分 ≥ 55",
                "query": {"val_score": {"min": 55}, "quality_score": {"min": 55}, "sort_by": "val_score"},
            },
            {
                "id": "policy_driven",
                "label": "政策驱动",
                "desc": "政策分 ≥ 60 且 综合分 ≥ 50",
                "query": {"policy_score": {"min": 60}, "score": {"min": 50}, "sort_by": "policy_score"},
            },
            {
                "id": "veto_watch",
                "label": "一票否决观察",
                "desc": "含 reduce / exclude 股票",
                "query": {"veto_status": ["reduce", "exclude"], "sort_by": "score"},
            },
        ]
    }
