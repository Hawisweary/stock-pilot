"""AI 深度分析引擎"""
import sqlite3, json
from config import DB_PATH, latest_trading_date
from services.llm_client import chat_completion, is_llm_available


def ai_deep_analyze(dimension: str, rule_score: dict, stock_info: dict) -> dict:
    if not is_llm_available():
        return {"error": "LLM 未配置"}

    prompts = {
        "capital": '你是一位A股量化分析师。请评估以下股票的资金面。股票: ' + stock_info.get("name","") + '(' + stock_info.get("code","") + ') 规则引擎评分: ' + str(rule_score.get("score", 50)) + '/100。请给出AI调整后的资金面评分(0-100)和一句话理由。严格输出JSON: {"score": 60, "reason": "..."}',
        "policy": '你是一位A股政策分析师。股票: ' + stock_info.get("name","") + '(' + stock_info.get("code","") + ') 规则引擎评分: ' + str(rule_score.get("composite_score", 50)) + '/100。行业: ' + stock_info.get("industry","未知") + '。请给出AI调整后的政策面评分(0-100)和一句话理由。严格输出JSON: {"score": 60, "reason": "..."}',
        "sentiment": '你是一位A股行为金融分析师。股票: ' + stock_info.get("name","") + '(' + stock_info.get("code","") + ') 规则引擎评分: ' + str(rule_score.get("score", 50)) + '/100。请给出AI调整后的情绪评分(0-100)和一句话理由。严格输出JSON: {"score": 60, "reason": "..."}',
    }

    prompt = prompts.get(dimension, "")
    if not prompt:
        return {"error": "不支持的维度"}

    try:
        text = chat_completion(prompt, max_tokens=300, temperature=0.3)
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            result = json.loads(text[start:end+1])
            return {
                "ai_score": max(0, min(100, result.get("score", rule_score.get("score", 50)))),
                "ai_reason": result.get("reason", ""),
                "original_score": rule_score.get("score", rule_score.get("composite_score", 50)),
            }
        return {"ai_score": rule_score.get("score", 50), "ai_reason": "LLM解析失败"}
    except Exception as e:
        return {"error": str(e)[:100]}


def sync_ai_score_to_project(stock_id: int, dimension: str, ai_score: float):
    from services.comprehensive_store import upsert_dimension_by_key

    try:
        calc_date = upsert_dimension_by_key(stock_id, dimension, ai_score)
    except ValueError as e:
        return {"synced": False, "error": str(e)}
    return {"synced": True, "dimension": dimension, "score": ai_score, "calc_date": calc_date}
