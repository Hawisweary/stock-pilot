"""AI 多角色辩论分析 — 对综合评分进行交叉验证"""
import sqlite3, json, re
from datetime import date

from config import DB_PATH


def debate_analysis(stock_id: int, code: str) -> dict:
    """3 角色辩论：多头、空头、裁判，对 8 维度评分进行交叉验证"""
    today = date.today().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    stock = conn.execute("SELECT code, name, industry_sw FROM stocks WHERE id=?", (stock_id,)).fetchone()
    if not stock:
        conn.close()
        return {"error": "股票不存在"}

    comp = conn.execute("""SELECT * FROM comprehensive_scores
        WHERE stock_id=? ORDER BY calc_date DESC LIMIT 1""", (stock_id,)).fetchone()
    if not comp:
        conn.close()
        return {"error": "暂无综合评分"}

    comp = dict(comp)

    # 收集新闻摘要
    news = conn.execute("""SELECT title, sentiment_label FROM stock_news
        WHERE stock_id=? ORDER BY pub_date DESC LIMIT 5""", (stock_id,)).fetchall()
    news_text = "\n".join(f"- {n['title']} [{n['sentiment_label'] or '?评分'}]" for n in news)

    conn.close()

    # 构建辩论 Prompt
    prompt = f"""扮演 3 个角色进行辩论分析。股票：{stock['code']} {stock['name']} 行业：{stock['industry_sw']}

=== 8 维度综合评分 ===
综合: {comp['composite_score']}分
基本面: {comp['fundamental_score']}分
技术面: {comp['technical_score']}分
新闻面: {comp['sentiment_score']}分
资金面: {comp['capital_score']}分
政策面: {comp['policy_score']}分
情绪面: {comp['mood_score']}分
估值面: {comp['val_score']}分

=== 近期新闻 ===
{news_text or '（无近期新闻）'}

请按以下格式输出纯JSON：

{{
  "bull": {{ "opinion": "多头观点50字", "score_adjust": +5~-5分, "key_reason": "核心理由" }},
  "bear": {{ "opinion": "空头观点50字", "score_adjust": +5~-5分, "key_reason": "核心理由" }},
  "judge": {{ "verdict": "最终判断20字", "final_score": 调整后综合分0-100, "confidence": 0-1, "risk": "高/中/低" }}
}}"""

    try:
        from services.news_fetcher import chat_completion
        text = chat_completion(prompt, system_prompt="你是投资辩论裁判，输出纯JSON。",
                              max_tokens=800, temperature=0.3)
        start = text.find("{")
        end = text.rfind("}") + 1
        debate = json.loads(text[start:end]) if 0 <= start < end else None
    except Exception as e:
        return {"error": f"LLM 调用失败: {e}", "stock_id": stock_id}

    if not debate:
        return {"error": "LLM 返回非 JSON"}

    # 写入结果
    result = {
        "stock_id": stock_id, "code": code, "name": stock["name"],
        "date": today, "original_score": comp["composite_score"],
        "debate": debate,
        "adjusted_score": debate.get("judge", {}).get("final_score", comp["composite_score"]),
    }

    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS debate_analyses (
        id INTEGER PRIMARY KEY AUTOINCREMENT, stock_id INTEGER, date TEXT,
        original_score REAL, adjusted_score REAL, debate_json TEXT,
        UNIQUE(stock_id, date))""")
    conn.execute("""INSERT OR REPLACE INTO debate_analyses
        (stock_id, date, original_score, adjusted_score, debate_json)
        VALUES (?,?,?,?,?)""",
        (stock_id, today, comp["composite_score"],
         debate["judge"]["final_score"] if "judge" in debate else comp["composite_score"],
         json.dumps(debate, ensure_ascii=False)))
    conn.commit(); conn.close()

    return result
