from config import DB_PATH

"""第3-5轮全部API — 财报+综述+研报+舆情+交易日志+仓位优化+导出"""
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from api_utils import execute_sql

router = APIRouter(prefix="/api", tags=["premium"])


# ── 财务报表 ──
@router.post("/financials/sync")
async def sync_all_financials():
    import threading
    def _run():
        stocks = execute_sql("SELECT id, code FROM stocks WHERE is_active=1")
        from services.premium import sync_financials
        for i, s in enumerate(stocks):
            try:
                r = sync_financials(s["id"], s["code"])
                print(f"[财报] {i+1}/{len(stocks)} {s['code']} synced={r['reports_synced']}")
            except Exception as e:
                print(f"[财报] {s['code']} 失败: {e}")
    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


@router.get("/financials/{stock_id}")
async def get_financials(stock_id: int):
    import sqlite3
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM financial_statements WHERE stock_id=? ORDER BY report_date DESC LIMIT 4",
                        (stock_id,)).fetchall()
    conn.close()
    return {"stock_id": stock_id, "reports": [dict(r) for r in rows]}


# ── 每日综述 ──
@router.post("/review/generate")
async def generate_review():
    from services.premium import generate_daily_review
    return generate_daily_review()


@router.get("/review/latest")
async def latest_review():
    import sqlite3
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM daily_reviews ORDER BY date DESC LIMIT 1").fetchone()
        conn.close()
        return {"review": dict(row) if row else None}
    except Exception:
        import logging; logging.getLogger(__name__).exception("daily_reviews query failed")
        conn.close()
        return {"review": None}


# ── 舆情热点 ──
@router.get("/hotspots")
async def hotspots():
    from services.premium import detect_hotspots
    return detect_hotspots()


# ── 交易日志（统一 portfolio_svc 流水）──
@router.get("/portfolio/{portfolio_id}/journal")
async def trade_journal(portfolio_id: int):
    from services.portfolio_svc import get_portfolio

    pf = get_portfolio(portfolio_id)
    if "error" in pf:
        raise HTTPException(status_code=404, detail=pf["error"])
    journal = pf.get("journal") or []
    sells = sum(1 for j in journal if j.get("action") == "SELL")
    return {
        "portfolio_id": portfolio_id,
        "trades": journal,
        "stats": {"total": len(journal), "sells": sells},
    }


@router.post("/portfolio/{portfolio_id}/journal")
async def add_journal_entry(
    portfolio_id: int,
    stock_code: str,
    action: str,
    trade_date: str,
    price: float,
    lots: int,
    reason: str = "",
    strategy: str = "manual",
):
    """手工记一笔流水（不影响持仓，仅备注）"""
    import sqlite3

    from services.portfolio_svc import _ensure_tables

    if lots <= 0 or lots % 100 != 0:
        raise HTTPException(status_code=400, detail="lots 须为 100 的整数倍")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    _ensure_tables(conn)
    stock = conn.execute(
        "SELECT id, code, name FROM stocks WHERE code=? AND is_active=1", (stock_code,)
    ).fetchone()
    if not stock:
        conn.close()
        raise HTTPException(status_code=400, detail="股票不在跟踪列表")
    conn.execute(
        """INSERT INTO trade_journal
           (portfolio_id, stock_id, action, shares, price, trade_date, code, name, reason, strategy, notes)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            portfolio_id,
            stock["id"],
            action.upper(),
            lots,
            price,
            trade_date,
            stock["code"],
            stock["name"],
            reason,
            strategy,
            "manual_entry",
        ),
    )
    conn.commit()
    conn.close()
    return {"status": "ok"}


# ── 仓位优化 ──
@router.post("/portfolio/optimize")
async def optimize(portfolio_id: int = None, stock_codes: str = Query(None), capital: float = 100000):
    from services.premium import optimize_portfolio

    codes = stock_codes.split(",") if stock_codes else []
    if portfolio_id and not codes:
        positions = execute_sql(
            """SELECT s.code FROM portfolio_positions pp
               JOIN stocks s ON pp.stock_id = s.id
               WHERE pp.portfolio_id=?""",
            (portfolio_id,),
        )
        codes = [p["code"] for p in positions]
    if not codes:
        codes = [r["code"] for r in execute_sql("SELECT code FROM stocks WHERE is_active=1 LIMIT 10")]
    return optimize_portfolio(codes, capital)


# ── 数据导出 ──
@router.get("/export/scores", response_class=PlainTextResponse)
async def export_scores(format: str = "csv"):
    from services.premium import export_scores_csv
    content = export_scores_csv()
    from fastapi.responses import Response
    return Response(content=content, media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=scores.csv"})


@router.get("/export/backtest")
async def export_backtest():
    import sqlite3
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM comprehensive_scores ORDER BY calc_date DESC LIMIT 500").fetchall()
    conn.close()
    headers = "stock_id,calc_date,composite,fundamental,technical,news,capital,policy,mood,valuation"
    lines = [headers]
    for r in rows:
        lines.append(f"{r['stock_id']},{r['calc_date']},{r['composite_score']},"
                     f"{r['fundamental_score']},{r['technical_score']},{r['sentiment_score']},"
                     f"{r['capital_score']},{r['policy_score']},{r['mood_score']},{r['val_score']}")
    from fastapi.responses import Response
    return Response(content="\n".join(lines), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=backtest.csv"})
