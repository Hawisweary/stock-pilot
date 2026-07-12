from config import DB_PATH
"""
AI 分析 API
- POST /api/ai/analyze/{stock_id}    生成 AI 基本面分析
- GET  /api/ai/history/{stock_id}    历史 AI 分析记录
"""
from fastapi import APIRouter, HTTPException
from api_utils import execute_sql, execute_insert
from api_models import AiAnalysisIn

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.post("/analyze/{stock_id}")
async def analyze_stock(stock_id: int, body: AiAnalysisIn = AiAnalysisIn()):
    """生成 AI 基本面分析"""
    stock = execute_sql("SELECT * FROM stocks WHERE id=? AND is_active=1", (stock_id,))
    if not stock:
        raise HTTPException(status_code=404, detail="股票不存在")

    s = stock[0]

    from services.ai_analyzer import AiAnalyzer
    analyzer = AiAnalyzer()
    input_hash = analyzer.financial_input_hash(stock_id)

    if not body.force:
        cached = execute_sql(
            """SELECT * FROM ai_analyses
               WHERE stock_id=? AND input_hash=? AND input_hash != ''
               ORDER BY generated_at DESC LIMIT 1""",
            (stock_id, input_hash),
        )
        if cached:
            out = _format_analysis(cached[0])
            out["source"] = cached[0].get("raw_response_json") and "llm" or "cache"
            out["cached"] = True
            return out
        recent = execute_sql(
            """SELECT id FROM ai_analyses
               WHERE stock_id=? AND generated_at > datetime('now', '-7 days')
               ORDER BY generated_at DESC LIMIT 1""",
            (stock_id,),
        )
        if recent:
            existing = execute_sql("SELECT * FROM ai_analyses WHERE id=?", (recent[0]["id"],))
            if existing:
                out = _format_analysis(existing[0])
                out["source"] = "llm"
                return out

    try:
        import asyncio as _asyncio
        result = await _asyncio.to_thread(analyzer.analyze, stock_id, s["code"], s["name"])

        import json as _json
        analysis_id = execute_insert(
            """INSERT INTO ai_analyses
               (stock_id, analysis_type, summary, strengths_json, weaknesses_json,
                factor_commentary_json, valuation_view, overall_rating, raw_response_json, input_hash)
               VALUES (?, 'fundamental', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                stock_id,
                result.get("summary", ""),
                _json.dumps(result.get("strengths", []), ensure_ascii=False),
                _json.dumps(result.get("weaknesses", []), ensure_ascii=False),
                _json.dumps(result.get("factor_commentary", {}), ensure_ascii=False),
                result.get("valuation_view", ""),
                result.get("overall_rating", ""),
                _json.dumps(result, ensure_ascii=False),
                input_hash,
            ),
        )

        row = execute_sql("SELECT * FROM ai_analyses WHERE id=?", (analysis_id,))
        formatted = _format_analysis(row[0]) if row else {}
        if formatted:
            formatted["source"] = result.get("source", "llm")
        return formatted

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 分析失败: {str(e)}")


@router.get("/history/{stock_id}")
async def analysis_history(stock_id: int, limit: int = 10):
    """获取历史 AI 分析记录"""
    rows = execute_sql(
        """SELECT * FROM ai_analyses
           WHERE stock_id=?
           ORDER BY generated_at DESC
           LIMIT ?""",
        (stock_id, limit)
    )
    return [_format_analysis(r) for r in rows]


def _format_analysis(row) -> dict:
    """格式化 AI 分析结果"""
    import json
    result = dict(row)
    # 解析 JSON 字段
    for field in ["strengths_json", "weaknesses_json", "factor_commentary_json", "raw_response_json"]:
        val = result.pop(field, "[]")
        try:
            result[field.replace("_json", "")] = json.loads(val) if isinstance(val, str) else val
        except json.JSONDecodeError:
            result[field.replace("_json", "")] = val
    return result
