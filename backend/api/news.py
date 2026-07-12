from config import DB_PATH

"""新闻面 API — 抓取 + 情感分析"""
from fastapi import APIRouter, HTTPException
from api_utils import execute_sql

router = APIRouter(prefix="/api/stocks", tags=["news"])


@router.post("/{stock_id}/news/fetch")
async def fetch_news(stock_id: int):
    """从东财抓取最新新闻"""
    stock = execute_sql("SELECT code FROM stocks WHERE id=? AND is_active=1", (stock_id,))
    if not stock:
        raise HTTPException(status_code=404, detail="股票不存在")

    from services.news_fetcher import fetch_news_for_stock
    added = fetch_news_for_stock(stock_id, stock[0]["code"])
    return {"stock_id": stock_id, "added": added, "message": f"新增 {added} 条新闻"}


@router.get("/{stock_id}/news")
async def list_news(stock_id: int, limit: int = 20):
    """获取新闻列表（含情感 + 缓存摘要）"""
    import sqlite3
    from services.news_fetcher import get_news_from_db
    news = get_news_from_db(stock_id, limit)

    # 查是否有缓存的情感分析结果
    sentiment = None
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT sentiment_score, sentiment_label, ai_summary
               FROM stock_news WHERE stock_id=? AND sentiment_score IS NOT NULL
               ORDER BY pub_date DESC LIMIT 1""", (stock_id,)
        ).fetchone()
        conn.close()
        if row:
            sentiment = {"score": row["sentiment_score"], "label": row["sentiment_label"],
                        "summary": row["ai_summary"] or "", "cached": True}
    except Exception:
        pass

    return {"stock_id": stock_id, "news": news, "count": len(news), "sentiment": sentiment}


@router.post("/{stock_id}/news/analyze")
async def analyze_news_sentiment(stock_id: int):
    """AI 分析新闻情感"""
    stock = execute_sql("SELECT code FROM stocks WHERE id=? AND is_active=1", (stock_id,))
    if not stock:
        raise HTTPException(status_code=404)

    from services.news_fetcher import fetch_news_for_stock, analyze_sentiment
    # 先抓取最新新闻
    fetch_news_for_stock(stock_id, stock[0]["code"])
    # 再分析
    result = analyze_sentiment(stock_id)
    if result.get("score") is not None:
        from services.comprehensive_store import upsert_dimension_score
        upsert_dimension_score(stock_id, "sentiment_score", float(result["score"]))
    return {"stock_id": stock_id, **result}


@router.post("/news/analyze-all")
async def analyze_all_news():
    """批量全量新闻抓取+情感分析（后台线程）"""
    import threading, time as _time

    def _run():
        stocks = execute_sql("SELECT id, code, name FROM stocks WHERE is_active=1")
        print(f"[NewsAI] 开始全量新闻分析 {len(stocks)} 只")
        from services.news_fetcher import fetch_news_for_stock, analyze_sentiment
        from services.comprehensive_store import upsert_dimension_score

        for i, s in enumerate(stocks):
            try:
                added = fetch_news_for_stock(s["id"], s["code"])
                if added > 0:
                    print(f"[NewsAI] {s['code']} +{added}条新闻")
                result = analyze_sentiment(s["id"])
                score = result.get("score")
                if score is not None:
                    upsert_dimension_score(s["id"], "sentiment_score", float(score))
                cached = result.get("cached", False)
                analyzed = result.get("analyzed", 0)
                if cached:
                    print(f"[NewsAI] {i+1}/{len(stocks)} {s['code']} 缓存命中 score={score}")
                elif analyzed > 0:
                    print(f"[NewsAI] {i+1}/{len(stocks)} {s['code']} 分析{analyzed}条 score={score}")
                elif result.get("error"):
                    print(f"[NewsAI] {i+1}/{len(stocks)} {s['code']} 错误: {result['error']}")
                else:
                    print(f"[NewsAI] {i+1}/{len(stocks)} {s['code']} 无新闻可分析")
                _time.sleep(1)
            except Exception as e:
                print(f"[NewsAI] {s['code']} 失败: {e}")
        print("[NewsAI] 全量完成")

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started", "message": "全量新闻分析已启动", "total": len(execute_sql("SELECT id FROM stocks WHERE is_active=1"))}

@router.get("/{stock_id}/announcements")
async def list_announcements(stock_id: int, limit: int = 30):
    """上市公司公告（东财 + 巨潮）"""
    from services.announcement_fetch import get_announcements_from_db

    stock = execute_sql("SELECT code FROM stocks WHERE id=? AND is_active=1", (stock_id,))
    if not stock:
        raise HTTPException(status_code=404, detail="股票不存在")
    rows = get_announcements_from_db(stock_id, limit=limit)
    return {"stock_id": stock_id, "announcements": rows, "count": len(rows)}


@router.post("/{stock_id}/announcements/fetch")
async def fetch_announcements(stock_id: int, limit: int = 30):
    """抓取并入库最新公告"""
    stock = execute_sql("SELECT code FROM stocks WHERE id=? AND is_active=1", (stock_id,))
    if not stock:
        raise HTTPException(status_code=404, detail="股票不存在")
    from services.announcement_fetch import sync_announcements

    added = sync_announcements(stock_id, stock[0]["code"], limit=limit)
    return {"stock_id": stock_id, "added": added, "message": f"新增 {added} 条公告"}


@router.post("/news/refresh-weighted")
async def refresh_weighted_news():
    """加权新闻评分 + 无新闻代理补全"""
    import threading, sqlite3, logging
    from datetime import date
    from services.news_fetcher import refresh_weighted_news_all, fill_news_gaps

    def _run():
        calc_date = date.today().strftime("%Y-%m-%d")
        r1 = refresh_weighted_news_all(calc_date)
        r2 = fill_news_gaps(calc_date)
        
        # v3.0: composite_score 写入已移除；V5 重算由 Path B 调度。
        logging.getLogger("news").info(f"Weighted refresh: {r1} | Gaps filled: {r2}")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return {"status": "started", "message": "加权新闻评分+无新闻代理补全已启动"}
