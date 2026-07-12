"""
schema_glossary.py — 列语义唯一机器可读源（v3.0）

用途：
  - 开发时参考，区分 DEPRECATED / DIMENSION_SCORE / AUTHORITATIVE
  - GET /api/system/health 返回 schema_glossary_version，无需建 _schema_meta 表
  - grep `COLUMN_SEMANTICS` 找到所有受管列

规则：
  - 只需人工维护此文件；无需同步到数据库
  - 新增/废弃列时同步更新 COLUMN_SEMANTICS + docs/SCORING_GLOSSARY.md
"""

SCHEMA_GLOSSARY_VERSION = "3.0.0"

# 语义类型枚举
DEPRECATED = "DEPRECATED"          # v3.0 停止写入；2周后API不返回
DIMENSION_SCORE = "DIMENSION_SCORE" # 维度子表内部分，V5 输入层，保留
AUTHORITATIVE = "AUTHORITATIVE"     # 唯一权威综合分

COLUMN_SEMANTICS: dict[str, dict] = {
    # ── 已废弃（八维聚合层） ────────────────────────────────────────────
    "comprehensive_scores.composite_score": {
        "type": DEPRECATED,
        "since": "v3.0",
        "description": "八维加权综合分；C1/H1 根因；v3.0 停止写入",
        "replaced_by": "comprehensive_scores.composite_v5",
    },
    "comprehensive_scores.debate_locked": {
        "type": DEPRECATED,
        "since": "v3.0",
        "description": "辩论锁标志；随辩论链路整体下线",
        "replaced_by": None,
    },
    "debate_v2.adjusted_score": {
        "type": DEPRECATED,
        "since": "v3.0",
        "description": "LLM 辩论微调综合分；与 composite_v5 脱节",
        "replaced_by": "comprehensive_scores.composite_v5",
    },
    "debate_v2.original_score": {
        "type": DEPRECATED,
        "since": "v3.0",
        "description": "辩论前原始分（即旧 composite_score）；随辩论链路下线",
        "replaced_by": None,
    },

    # ── 维度内部分（V5 输入层，保留） ───────────────────────────────────
    "factor_scores.composite_score": {
        "type": DIMENSION_SCORE,
        "description": "基本面维度分（ROE/毛利率/净利率/ROIC 加权）→ V5 fundamental tier",
        "v5_dimension": "fundamental",
    },
    "valuation_scores.composite_score": {
        "type": DIMENSION_SCORE,
        "description": "估值维度分（PE/PB/股息率/PEG）→ V5 valuation tier",
        "v5_dimension": "valuation",
    },
    "capital_scores.composite_score": {
        "type": DIMENSION_SCORE,
        "description": "资金面维度分（主力净流/融资余额等）→ V5 capital tier",
        "v5_dimension": "capital",
    },
    "policy_scores.composite_score": {
        "type": DIMENSION_SCORE,
        "description": "政策维度分（关键词/LLM/补贴）→ V5 policy tier",
        "v5_dimension": "policy",
    },
    "sentiment_scores.composite_score": {
        "type": DIMENSION_SCORE,
        "description": "情绪维度分（换手/量比/乖离）→ V5 mood 回退源",
        "v5_dimension": "mood_fallback",
    },
    "tech_analysis_cache.score": {
        "type": DIMENSION_SCORE,
        "description": "技术维度分（均线/KDJ/MACD 规则引擎）→ V5 technical tier",
        "v5_dimension": "technical",
    },

    # ── 权威综合分（唯一） ───────────────────────────────────────────────
    "comprehensive_scores.composite_v5": {
        "type": AUTHORITATIVE,
        "description": "十维 tier + 短板惩罚 + veto；v3.0 唯一权威综合分",
        "write_path": "v5_scorer.compute_stock_v5_tiers → persist_v5_score",
        "api_field": "score",
    },
    "comprehensive_scores.v5_breakdown_json": {
        "type": AUTHORITATIVE,
        "description": "十维详情 JSON；每维含 source/status/tier/score；breakdown 必写",
    },
    "comprehensive_scores.veto_status": {
        "type": AUTHORITATIVE,
        "description": "'ok' | 'excluded'；基本面硬否决触发时为 excluded",
    },
    "v_stock_scores.score": {
        "type": AUTHORITATIVE,
        "description": "v_stock_scores 视图别名，= composite_v5；API 排名/列表统一查询入口",
        "migration": "v34",
        "created": "2026-06-21",
    },
    "v_stock_scores.veto_status": {
        "type": AUTHORITATIVE,
        "description": "视图暴露的 veto 状态：'ok'|'discount'|'exclude'",
        "migration": "v34",
    },
    "v_stock_scores.breakdown_json": {
        "type": AUTHORITATIVE,
        "description": "视图暴露的十维 V5 breakdown JSON",
        "migration": "v34",
    },
    # ── 新增维度列（v3.0 V5 维度层，写入 comprehensive_scores） ──────────
    "comprehensive_scores.quality_score": {
        "type": DIMENSION_SCORE,
        "description": "质量维度分（利息保障/资产负债率等）→ V5 quality tier",
        "v5_dimension": "quality",
    },
    "comprehensive_scores.industry_score": {
        "type": DIMENSION_SCORE,
        "description": "行业维度分（行业景气/EPS修正率）→ V5 industry tier",
        "v5_dimension": "industry",
    },
    "comprehensive_scores.market_env_score": {
        "type": DIMENSION_SCORE,
        "description": "市场环境分（Beta/涨跌幅/大盘动量）→ V5 market_env tier",
        "v5_dimension": "market_env",
    },
}


def get_semantic(table_col: str) -> dict:
    """查询列语义，table_col 格式 'table.column'"""
    return COLUMN_SEMANTICS.get(table_col, {"type": "UNKNOWN", "description": "未记录"})


def list_by_type(semantic_type: str) -> list[str]:
    """列出指定语义类型的所有列"""
    return [k for k, v in COLUMN_SEMANTICS.items() if v.get("type") == semantic_type]


if __name__ == "__main__":
    print(f"Schema Glossary v{SCHEMA_GLOSSARY_VERSION}\n")
    for t in (DEPRECATED, DIMENSION_SCORE, AUTHORITATIVE):
        cols = list_by_type(t)
        print(f"[{t}] ({len(cols)} 列)")
        for c in cols:
            desc = COLUMN_SEMANTICS[c].get("description", "")
            print(f"  {c}: {desc}")
        print()
