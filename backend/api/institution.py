from config import DB_PATH

"""机构面 API"""
from fastapi import APIRouter, HTTPException
from api_utils import execute_sql

router = APIRouter(prefix="/api/stocks", tags=["institution"])


@router.post("/{stock_id}/institution/analyze")
async def analyze_institution(stock_id: int):
    stock = execute_sql("SELECT code, name FROM stocks WHERE id=? AND is_active=1", (stock_id,))
    if not stock: raise HTTPException(status_code=404)
    from services.institution_scorer import compute_institution_score
    return compute_institution_score(stock_id, stock[0]["code"])


@router.get("/{stock_id}/institution")
async def get_institution(stock_id: int):
    import sqlite3, json
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM institution_scores WHERE stock_id=? ORDER BY date DESC LIMIT 1",
        (stock_id,)).fetchone()
    conn.close()
    if not row: return {"stock_id": stock_id, "score": None}
    result = dict(row)
    if result.get("breakdown_json"):
        result["breakdown"] = json.loads(result.pop("breakdown_json"))
    return {"stock_id": stock_id, "score": result}


@router.post("/institution/analyze-all")
async def analyze_all_institution():
    import threading, time as _time
    def _run():
        import socket; socket.setdefaulttimeout(8)
        stocks = execute_sql("SELECT id,code,name FROM stocks WHERE is_active=1")
        from services.institution_scorer import compute_institution_score
        for i, s in enumerate(stocks):
            try:
                r = compute_institution_score(s["id"], s["code"])
                print(f"[机构] {i+1}/{len(stocks)} {s['code']} score={r.get('composite_score','?')}")
                _time.sleep(0.3)
            except Exception as e:
                print(f"[机构] {s['code']} 失败: {e}")
    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started", "message": "全量机构面分析已启动"}
