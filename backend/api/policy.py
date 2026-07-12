from config import DB_PATH

"""政策面 API"""
from fastapi import APIRouter, HTTPException
from api_utils import execute_sql

router = APIRouter(prefix="/api/stocks", tags=["policy"])


@router.post("/{stock_id}/policy/analyze")
async def analyze_policy(stock_id: int, ai: bool = False):
    stock = execute_sql("SELECT code, name, industry_sw FROM stocks WHERE id=? AND is_active=1", (stock_id,))
    if not stock: raise HTTPException(status_code=404)
    from services.policy_scorer import compute_policy_score
    result = compute_policy_score(stock_id, stock[0]["code"])
    score_val = result.get("composite_score", result) if isinstance(result, dict) else result
    if isinstance(score_val, (int, float)):
        from services.comprehensive_store import upsert_dimension_score
        upsert_dimension_score(stock_id, "policy_score", float(score_val))
    # AI 深度增强
    if ai:
        from services.ai_deep import ai_deep_analyze, sync_ai_score_to_project
        ai_r = ai_deep_analyze("policy", {"composite_score": score_val, "keywords": []}, {"code": stock[0]["code"], "name": stock[0]["name"], "industry": stock[0].get("industry_sw", "")})
        if "error" not in ai_r:
            sync_ai_score_to_project(stock_id, "policy", ai_r["ai_score"])
            return {"stock_id": stock_id, "rule_score": score_val, "ai_score": ai_r["ai_score"], "ai_reason": ai_r["ai_reason"]}
        return {"stock_id": stock_id, "score": score_val, "ai_error": ai_r["error"]}
    return {"stock_id": stock_id, "score": score_val}


@router.get("/{stock_id}/policy")
async def get_policy(stock_id: int):
    import json
    from services.policy_scorer import compute_policy_score
    stock = execute_sql("SELECT code FROM stocks WHERE id=? AND is_active=1", (stock_id,))
    if not stock: raise HTTPException(status_code=404)
    result = compute_policy_score(stock_id, stock[0]["code"])
    val = result.get("composite_score", result) if isinstance(result, dict) else result
    return {"stock_id": stock_id, "composite_score": val}


@router.post("/policy/analyze-all")
async def analyze_all_policy():
    import threading, sqlite3, json
    from datetime import date

    def _run():
        stocks = execute_sql("SELECT id,code,name FROM stocks WHERE is_active=1")
        from services.policy_scorer import compute_policy_score
        from services.comprehensive_store import upsert_dimension_score
        from database import get as get_db, write_lock
        today_str = date.today().strftime("%Y-%m-%d")
        for i, s in enumerate(stocks):
            try:
                r = compute_policy_score(s["id"], s["code"])
                score = r.get("composite_score", 50)
                with write_lock:
                    conn = get_db()
                    conn.execute(
                        """INSERT OR REPLACE INTO policy_scores
                        (stock_id, date, composite_score, breakdown_json)
                        VALUES (?, ?, ?, ?)""",
                        (s["id"], today_str, score, json.dumps(r.get("keywords", []), ensure_ascii=False)),
                    )
                    conn.commit()
                upsert_dimension_score(s["id"], "policy_score", float(score))
                llm = f"LLM={r['llm_score']}" if r.get("llm_score") else "KW-only"
                print(f"[Policy] {i+1}/{len(stocks)} {s['code']} score={score} ({llm})")
            except Exception as e:
                print(f"[Policy] {s['code']} 失败: {e}")
    threading.Thread(target=_run, daemon=True).start()
    total = len(execute_sql("SELECT id FROM stocks WHERE is_active=1"))
    return {"status": "started", "message": "全量政策面分析已启动", "total": total}


@router.post("/policy/global-scan")
async def global_policy_scan():
    """全局政策扫描 — 1次 LLM 调用覆盖所有行业"""
    import traceback, importlib, sys
    try:
        # 强制重新加载 policy_global 模块以获取最新代码
        if "services.policy_global" in sys.modules:
            importlib.reload(sys.modules["services.policy_global"])
        from services.policy_global import global_policy_scan as do_scan
        result = do_scan()
        if "error" in result:
            return result
        # 扫描完成后，为每只股票写入 policy_scores + comprehensive_scores
        # 关键修复：不使用 upsert_dimension_score（它会打开第二个连接，WAL模式下死锁）
        # 直接用同一个连接批量写入
        import json, datetime
        stocks = execute_sql("SELECT id, code FROM stocks WHERE is_active=1")
        from database import get as get_db, write_lock
        from services.policy_global import get_policy_for_stock
        today = datetime.date.today().strftime("%Y-%m-%d")
        updated = 0
        with write_lock:
            conn = get_db()
            for s in stocks:
                ps = get_policy_for_stock(s["id"])
                if ps and ps.get("score"):
                    # 写入 policy_scores 表
                    conn.execute("""INSERT OR REPLACE INTO policy_scores
                        (stock_id, date, composite_score, breakdown_json)
                        VALUES (?, ?, ?, ?)""",
                        (s["id"], today, ps["score"],
                         json.dumps({"source": "global_scan", "tendency": ps.get("tendency")})))
                    # 直接写入 comprehensive_scores（避免 upsert_dimension_score 开新连接）
                    existing = conn.execute(
                        "SELECT id FROM comprehensive_scores WHERE stock_id=? AND calc_date=?",
                        (s["id"], today),
                    ).fetchone()
                    if existing:
                        conn.execute(
                            "UPDATE comprehensive_scores SET policy_score=? WHERE stock_id=? AND calc_date=?",
                            (float(ps["score"]), s["id"], today),
                        )
                    else:
                        conn.execute(
                            "INSERT INTO comprehensive_scores (stock_id, calc_date, policy_score) VALUES (?, ?, ?)",
                            (s["id"], today, float(ps["score"])),
                        )
                    updated += 1
            conn.commit()
        return {**result, "stocks_updated": updated, "date": today}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
