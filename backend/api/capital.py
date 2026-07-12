from config import DB_PATH

"""资金面 API — 主力推断 + 量价分析"""
from fastapi import APIRouter, HTTPException
from api_utils import execute_sql

router = APIRouter(prefix="/api/stocks", tags=["capital"])

_analyze_all_status = {
    "running": False,
    "finished": False,
    "progress": "0/0",
    "started_at": None,
    "finished_at": None,
    "errors": [],
}


@router.post("/{stock_id}/capital/analyze")
async def analyze_capital(stock_id: int, ai: bool = False):
    """资金面评分分析（ai=true 触发 LLM 深度分析）"""
    stock = execute_sql("SELECT code, name, industry_sw FROM stocks WHERE id=? AND is_active=1", (stock_id,))
    if not stock:
        raise HTTPException(status_code=404)

    from services.capital_scorer import compute_all_capital
    from datetime import date
    from services.comprehensive_store import upsert_dimension_score, resolve_calc_date
    from services.score_cache import persist_capital_rows
    import sqlite3

    today = date.today().strftime("%Y-%m-%d")
    all_results = compute_all_capital(today)
    result = next((r for r in all_results if r["stock_id"] == stock_id), None)
    if not result:
        raise HTTPException(status_code=404, detail="资金数据不足")

    conn = sqlite3.connect(DB_PATH)
    calc_date = resolve_calc_date(conn, stock_id)
    conn.close()
    persist_capital_rows([result], today)
    upsert_dimension_score(stock_id, "capital_score", float(result.get("composite_score", 0)), calc_date=calc_date)

    if ai:
        from services.ai_deep import ai_deep_analyze, sync_ai_score_to_project

        ai_result = ai_deep_analyze(
            "capital",
            result,
            {"code": stock[0]["code"], "name": stock[0]["name"], "industry": stock[0].get("industry_sw", "")},
        )
        if "error" not in ai_result:
            sync_ai_score_to_project(stock_id, "capital", ai_result["ai_score"])
            result["ai_score"] = ai_result["ai_score"]
            result["ai_reason"] = ai_result["ai_reason"]
            result["original_score"] = ai_result["original_score"]
        else:
            result["ai_error"] = ai_result["error"]
    return result


@router.get("/{stock_id}/capital")
async def get_capital(stock_id: int):
    """获取资金面最新评分（读缓存）"""
    stock = execute_sql("SELECT code FROM stocks WHERE id=? AND is_active=1", (stock_id,))
    if not stock:
        raise HTTPException(status_code=404)

    from services.capital_scorer import compute_capital_score

    score_data = compute_capital_score(stock_id, stock[0]["code"])
    if "error" in score_data:
        return {"stock_id": stock_id, "composite_score": 0}
    return {
        "stock_id": stock_id,
        "composite_score": score_data.get("score", 0),
        "flow_score": score_data.get("flow_score", 0),
        "turn_score": score_data.get("turn_score", 0),
        "change_score": score_data.get("change_score", 0),
    }


@router.post("/capital/analyze-all")
async def analyze_all_capital():
    """批量全量资金面分析（一次行情拉取 + 全局分位）"""
    import threading
    from datetime import date, datetime

    if _analyze_all_status["running"]:
        return {"status": "already_running", "progress": _analyze_all_status["progress"]}

    def _run():
        from services.capital_scorer import compute_all_capital
        from services.score_cache import persist_capital_rows, sync_comprehensive_column

        _analyze_all_status["running"] = True
        _analyze_all_status["finished"] = False
        _analyze_all_status["errors"] = []
        _analyze_all_status["started_at"] = datetime.now().isoformat()
        today_str = date.today().strftime("%Y-%m-%d")
        try:
            results = compute_all_capital(today_str)
            _analyze_all_status["progress"] = f"{len(results)}/{len(results)}"
            n = persist_capital_rows(results, today_str)
            synced = sync_comprehensive_column("capital_scores", "capital_score", today_str)
            print(f"[Capital] 批量完成: {n} 写入, {synced} 同步综合分")
        except Exception as e:
            _analyze_all_status["errors"].append(str(e))
            print(f"[Capital] 批量失败: {e}")
        finally:
            _analyze_all_status["running"] = False
            _analyze_all_status["finished"] = True
            _analyze_all_status["finished_at"] = datetime.now().isoformat()

    threading.Thread(target=_run, daemon=True).start()
    total = len(execute_sql("SELECT id FROM stocks WHERE is_active=1"))
    return {"status": "started", "message": "全量资金面分析已启动（批量模式）", "total": total}


@router.get("/capital/analyze-status")
async def get_analyze_status():
    """查询全量资金面分析状态"""
    return _analyze_all_status
