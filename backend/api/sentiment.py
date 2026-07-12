from config import DB_PATH as _DB_PATH

"""情绪面 API"""
from fastapi import APIRouter, HTTPException
from api_utils import execute_sql

DB = str(_DB_PATH)

router = APIRouter(prefix="/api/stocks", tags=["sentiment"])


@router.post("/{stock_id}/sentiment/analyze")
async def analyze_sentiment(stock_id: int, ai: bool = False):
    stock = execute_sql("SELECT code, name, industry_sw FROM stocks WHERE id=? AND is_active=1", (stock_id,))
    if not stock:
        raise HTTPException(status_code=404)
    from services.sentiment_scorer import compute_all_sentiment
    from datetime import date
    from services.comprehensive_store import upsert_dimension_score, resolve_calc_date
    from services.score_cache import persist_sentiment_rows
    import sqlite3

    today = date.today().strftime("%Y-%m-%d")
    all_results = compute_all_sentiment(today)
    result = next((r for r in all_results if r["stock_id"] == stock_id), None)
    if not result:
        raise HTTPException(status_code=404, detail="情绪数据不足")

    conn = sqlite3.connect(DB)
    calc_date = resolve_calc_date(conn, stock_id)
    conn.close()
    persist_sentiment_rows([result], today)
    upsert_dimension_score(stock_id, "mood_score", float(result.get("composite_score", 0)), calc_date=calc_date)

    if ai:
        from services.ai_deep import ai_deep_analyze, sync_ai_score_to_project

        ai_r = ai_deep_analyze(
            "sentiment",
            result,
            {"code": stock[0]["code"], "name": stock[0]["name"], "industry": stock[0].get("industry_sw", "")},
        )
        if "error" not in ai_r:
            sync_ai_score_to_project(stock_id, "sentiment", ai_r["ai_score"])
            result["ai_score"] = ai_r["ai_score"]
            result["ai_reason"] = ai_r["ai_reason"]
        else:
            result["ai_error"] = ai_r["error"]
    return result


@router.get("/{stock_id}/sentiment")
async def get_sentiment(stock_id: int):
    stock = execute_sql("SELECT code FROM stocks WHERE id=? AND is_active=1", (stock_id,))
    if not stock:
        raise HTTPException(status_code=404)
    from services.sentiment_scorer import compute_sentiment_score

    result = compute_sentiment_score(stock_id, stock[0]["code"])
    if "error" in result:
        return {"stock_id": stock_id, "composite_score": 0}
    return {
        "stock_id": stock_id,
        "composite_score": result.get("score", 0),
        "turn_score": result.get("turn_score", 0),
        "vol_score": result.get("vol_score", 0),
    }


@router.post("/sentiment/analyze-all")
async def analyze_all_sentiment():
    import threading
    from datetime import date as _date

    def _run():
        from services.sentiment_scorer import compute_all_sentiment
        from services.score_cache import persist_sentiment_rows, sync_comprehensive_column

        today_str = _date.today().strftime("%Y-%m-%d")
        try:
            results = compute_all_sentiment(today_str)
            n = persist_sentiment_rows(results, today_str)
            synced = sync_comprehensive_column("sentiment_scores", "mood_score", today_str)
            print(f"[Sentiment] 批量完成: {n} 写入, {synced} 同步综合分")
        except Exception as e:
            print(f"[Sentiment] 批量分析失败: {e}")
            import traceback

            traceback.print_exc()

    threading.Thread(target=_run, daemon=True).start()
    total = len(execute_sql("SELECT id FROM stocks WHERE is_active=1"))
    return {"status": "started", "message": "全量情绪面分析已启动（批量模式）", "total": total}
