"""新闻抓取 + AI 情感分析"""
import sqlite3
import json
from services.llm_client import chat_completion, is_llm_available

from config import DB_PATH, DEFAULT_SCORE

_KEYWORD_POSITIVE = (
    "增长", "盈利", "突破", "中标", "回购", "增持", "利好", "创新高", "超预期",
    "分红", "扩产", "签约", "获批", "涨停", "景气", "复苏", "上调", "买入",
)
_KEYWORD_NEGATIVE = (
    "亏损", "下滑", "减持", "立案", "警示", "违规", "下调", "暴雷", "退市",
    "诉讼", "处罚", "利空", "跌停", "质押", "违约", "停产", "调查", "卖出",
)


def _insert_news_rows(stock_id: int, articles: list[dict]) -> int:
    if not articles:
        return 0
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    added = 0
    for a in articles:
        title = str(a.get("title", ""))[:200]
        if not title:
            continue
        try:
            conn.execute(
                """INSERT OR IGNORE INTO stock_news (stock_id, title, content, source, pub_date, url)
                   VALUES (?,?,?,?,?,?)""",
                (
                    stock_id,
                    title,
                    str(a.get("content", ""))[:500],
                    str(a.get("source", "")),
                    str(a.get("pub_date", a.get("time", "")))[:10],
                    str(a.get("url", "")),
                ),
            )
            if conn.total_changes > 0:
                added += 1
        except Exception:
            pass
    conn.commit()
    conn.close()
    return added


def fetch_news_for_stock(stock_id: int, code: str, limit: int = 10) -> int:
    """抓取新闻存入 DB（东财直连 → akshare fallback），返回新增数"""
    articles: list[dict] = []

    try:
        from services.data_sources import eastmoney_stock_news

        for a in eastmoney_stock_news(code, page_size=limit):
            articles.append(
                {
                    "title": a.get("title", ""),
                    "content": a.get("content", ""),
                    "pub_date": str(a.get("pub_date", a.get("time", "")))[:10],
                    "source": a.get("source", "eastmoney"),
                    "url": a.get("url", ""),
                }
            )
    except Exception:
        pass

    if not articles:
        try:
            from services.akshare_lazy import akshare as _ak

            df = _ak().stock_news_em(symbol=code)
            if df is not None and len(df) > 0:
                col_map = {
                    "新闻标题": "title",
                    "新闻内容": "content",
                    "发布时间": "pub_date",
                    "文章来源": "source",
                    "新闻链接": "url",
                    "股票代码": "code",
                }
                df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
                if "pub_date" in df.columns:
                    df["pub_date"] = df["pub_date"].astype(str).str[:10]
                for _, row in df.head(limit).iterrows():
                    articles.append(dict(row))
        except Exception:
            pass

    added = _insert_news_rows(stock_id, articles)
    if added > 0:
        try:
            from services.event_classifier import classify_news

            classify_news([stock_id], limit_per_stock=max(limit * 3, 30))
        except Exception:
            pass
    return added


def sync_all_news(
    stock_ids: list[int] | None = None,
    *,
    limit: int = 15,
    sleep_s: float = 0.25,
) -> dict:
    """全市场/指定股票批量抓取新闻并触发事件分类。"""
    import time

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        if stock_ids:
            ph = ",".join("?" * len(stock_ids))
            rows = conn.execute(
                f"""SELECT id, code FROM stocks
                    WHERE id IN ({ph}) AND is_active=1 ORDER BY id""",
                stock_ids,
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, code FROM stocks WHERE is_active=1 ORDER BY id"
            ).fetchall()
    finally:
        conn.close()

    added = 0
    errors: list[str] = []
    for row in rows:
        sid, code = int(row["id"]), row["code"]
        try:
            added += fetch_news_for_stock(sid, code, limit=limit)
        except Exception as e:
            errors.append(f"{code}:{e}")
        time.sleep(sleep_s)

    return {
        "stocks": len(rows),
        "added": added,
        "limit_per_stock": limit,
        "errors": errors[:10],
    }


def score_text_keywords(text: str) -> tuple[float, str]:
    """规则引擎情感分（无 LLM 时使用）。"""
    if not text:
        return DEFAULT_SCORE, "中性"
    pos = sum(1 for k in _KEYWORD_POSITIVE if k in text)
    neg = sum(1 for k in _KEYWORD_NEGATIVE if k in text)
    if pos == neg == 0:
        return DEFAULT_SCORE, "中性"
    raw = 50 + (pos - neg) * 8
    score = max(5.0, min(95.0, float(raw)))
    if score >= 65:
        label = "偏多"
    elif score >= 55:
        label = "中性"
    elif score >= 45:
        label = "中性"
    elif score >= 35:
        label = "偏空"
    else:
        label = "强烈利空"
    return round(score, 1), label


def analyze_sentiment_keyword(stock_id: int) -> dict:
    """对未评分新闻做关键词情感分析并写回 DB。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT id, title, content FROM stock_news
           WHERE stock_id=? AND sentiment_score IS NULL
           ORDER BY pub_date DESC LIMIT 20""",
        (stock_id,),
    ).fetchall()
    if not rows:
        conn.close()
        return {"analyzed": 0, "method": "keyword"}

    analyzed = 0
    for r in rows:
        text = f"{r['title']} {r['content'] or ''}"
        score, label = score_text_keywords(text)
        conn.execute(
            "UPDATE stock_news SET sentiment_label=?, sentiment_score=? WHERE id=?",
            (label, score, r["id"]),
        )
        analyzed += 1
    conn.commit()
    conn.close()
    return {"analyzed": analyzed, "method": "keyword"}


def fetch_and_analyze_sentiment(stock_id: int, code: str) -> dict:
    """单股：拉新闻 → LLM 或关键词评分。"""
    added = fetch_news_for_stock(stock_id, code)
    if is_llm_available():
        result = analyze_sentiment(stock_id)
        result["news_added"] = added
        return result
    result = analyze_sentiment_keyword(stock_id)
    result["news_added"] = added
    return result


def touch_sentiment_refresh(stock_ids: list[int], scores: dict[int, float]) -> int:
    """写入今日 dated 情绪行，清除 stale 源日期检测。"""
    if not stock_ids:
        return 0
    conn = sqlite3.connect(DB_PATH)
    touched = 0
    for sid in stock_ids:
        score = scores.get(sid)
        if score is None:
            continue
        conn.execute(
            """INSERT INTO stock_news
               (stock_id, title, content, source, pub_date, sentiment_score, sentiment_label)
               VALUES (?,?,?,?,date('now'),?,?)""",
            (sid, "[刷新] 情绪加权", "", "refresh", score, "中性"),
        )
        touched += 1
    conn.commit()
    conn.close()
    return touched


def fetch_and_analyze_sentiment_batch(stock_ids=None) -> dict:
    """批量补新闻源并评分；仍无新闻时用行业 peer 代理写入 synthetic 行。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    if stock_ids:
        ph = ",".join(["?"] * len(stock_ids))
        stocks = conn.execute(
            f"SELECT id, code, industry_sw FROM stocks WHERE id IN ({ph}) AND is_active=1",
            tuple(stock_ids),
        ).fetchall()
    else:
        stocks = conn.execute(
            "SELECT id, code, industry_sw FROM stocks WHERE is_active=1"
        ).fetchall()
    conn.close()

    fetched = 0
    analyzed = 0
    peer_filled = 0
    still_empty: list[int] = []
    errors: list[dict] = []

    for s in stocks:
        sid = int(s["id"])
        try:
            r = fetch_and_analyze_sentiment(sid, s["code"])
            fetched += int(r.get("news_added", 0))
            analyzed += int(r.get("analyzed", 0))
        except Exception as e:
            errors.append({"stock_id": sid, "reason": str(e)[:120]})
            continue

        conn = sqlite3.connect(DB_PATH)
        has_scored = conn.execute(
            "SELECT COUNT(*) FROM stock_news WHERE stock_id=? AND sentiment_score IS NOT NULL",
            (sid,),
        ).fetchone()[0]
        if has_scored <= 0:
            ind = s["industry_sw"] or ""
            peer = conn.execute(
                """SELECT n.stock_id, COUNT(*) AS cnt
                   FROM stock_news n JOIN stocks s ON n.stock_id=s.id
                   WHERE s.industry_sw=? AND s.is_active=1 AND n.stock_id!=?
                     AND n.sentiment_score IS NOT NULL
                   GROUP BY n.stock_id ORDER BY cnt DESC LIMIT 1""",
                (ind, sid),
            ).fetchone()
            if peer:
                ps = compute_weighted_news_score(int(peer["stock_id"]))
                score = round(ps["score"] * 0.7 + DEFAULT_SCORE * 0.3, 1)
                conn.execute(
                    """INSERT INTO stock_news
                       (stock_id, title, content, source, pub_date, sentiment_score, sentiment_label)
                       VALUES (?,?,?,?,date('now'),?,?)""",
                    (
                        sid,
                        f"[行业代理] {ind or '同业'}情绪参考",
                        "",
                        "peer_proxy",
                        score,
                        "中性",
                    ),
                )
                conn.commit()
                peer_filled += 1
            else:
                conn.execute(
                    """INSERT INTO stock_news
                       (stock_id, title, content, source, pub_date, sentiment_score, sentiment_label)
                       VALUES (?,?,?,?,date('now'),?,?)""",
                    (sid, "[默认] 暂无新闻", "", "default", DEFAULT_SCORE, "中性"),
                )
                conn.commit()
                peer_filled += 1
        conn.close()

        conn2 = sqlite3.connect(DB_PATH)
        has_after = conn2.execute(
            "SELECT COUNT(*) FROM stock_news WHERE stock_id=? AND sentiment_score IS NOT NULL",
            (sid,),
        ).fetchone()[0]
        conn2.close()
        if has_after <= 0:
            still_empty.append(sid)

    refresh_scores: dict[int, float] = {}
    if stock_ids:
        conn3 = sqlite3.connect(DB_PATH)
        from services.sentiment_aggregate import resolve_sentiment_scores

        refresh_scores = resolve_sentiment_scores(conn3, list(stock_ids), __import__("config").latest_trading_date())
        conn3.close()
        touch_sentiment_refresh(list(stock_ids), refresh_scores)

    return {
        "attempted": len(stocks),
        "news_added": fetched,
        "analyzed": analyzed,
        "peer_filled": peer_filled,
        "refreshed": len(refresh_scores),
        "still_empty": still_empty,
        "errors": errors,
    }


def get_news_from_db(stock_id: int, limit: int = 20) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""SELECT title, content, source, pub_date, url, sentiment_score, sentiment_label
                 FROM stock_news WHERE stock_id=? ORDER BY pub_date DESC LIMIT ?""",
              (stock_id, limit))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def analyze_sentiment(stock_id: int) -> dict:
    """用 LLM 分析最近 N 条新闻的情感（带缓存：同一标题不重复分析）"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # 先查已分析过的整体结果（最新一条有评分的新闻）
    c.execute("""SELECT sentiment_score as score, sentiment_label as label,
                 ai_summary as summary FROM stock_news
                 WHERE stock_id=? AND sentiment_score IS NOT NULL
                 ORDER BY pub_date DESC LIMIT 1""", (stock_id,))
    cached = c.fetchone()
    cached_score = cached["score"] if cached else None

    # 找出未分析的新闻
    c.execute("""SELECT id, title, content, pub_date FROM stock_news
                 WHERE stock_id=? AND sentiment_score IS NULL
                 ORDER BY pub_date DESC LIMIT 15""", (stock_id,))
    to_analyze = c.fetchall()

    if not to_analyze:
        if cached_score is not None:
            conn.close()
            return {"cached": True, "analyzed": 0, "overall": cached["label"] or "中性",
                    "score": cached_score, "summary": cached["summary"] or ""}
        conn.close()
        return {"analyzed": 0}

    if not is_llm_available():
        conn.close()
        return {"error": "LLM 未配置"}

    # 构建 prompt
    titles = "\n".join([f"{i+1}. {r['title']}" for i, r in enumerate(to_analyze)])
    prompt = f"""分析以下近期股票新闻情感，返回 JSON（不要其他文字）：

{{
  "overall": "强烈利多/偏多/中性/偏空/强烈利空",
  "score": 0-100（强烈利多>80, 利多60-80, 中性40-60, 利空20-40, 强烈利空<20),
  "summary": "20字以内的整体判断，提及核心事件",
  "heat": "高/中/低（多条同主题=高, 零散=低）",
  "timeline": "突发/持续/平淡（集中爆发=突发, 渐进=持续）",
  "items": [
    {{"idx": 1, "sentiment": "强烈利多/利多/中性/利空/强烈利空", "score": 0-100}}
  ]
}}

新闻列表：
{titles}
"""
    try:
        text = chat_completion(prompt, system_prompt="你是A股新闻情感分析专家，按五级情感输出纯JSON。",
                              max_tokens=800, temperature=0.1)
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            conn.close()
            return {"error": "解析失败", "raw": text[:100]}
        result = json.loads(text[start:end+1])
        items = result.get("items", [])

        # 写入情感分数 + 整体摘要
        for item in items:
            idx = item.get("idx", 0) - 1
            if 0 <= idx < len(to_analyze):
                r = to_analyze[idx]
                conn.execute(
                    "UPDATE stock_news SET sentiment_label=?, sentiment_score=? WHERE id=?",
                    (item.get("sentiment", "neutral"), item.get("score", 50), r["id"])
                )
        # 只填充未逐条分析的行（保留个体精准标签）
        overall = result.get("overall", "中性")
        score = result.get("score", 50)
        summary = result.get("summary", "")
        conn.execute(
            "UPDATE stock_news SET sentiment_label=?, sentiment_score=?, ai_summary=? WHERE stock_id=? AND sentiment_score IS NULL",
            (overall, score, summary, stock_id)
        )
        conn.commit()
        conn.close()
        return {"analyzed": len(to_analyze), "overall": overall, "score": score, "summary": summary,
                "heat": result.get("heat", "中"), "timeline": result.get("timeline", "平淡")}
    except Exception as e:
        conn.close()
        return {"error": str(e)[:100]}

def compute_weighted_news_score(stock_id: int) -> dict:
    """V2: 源权重+时间衰减的新闻情感得分"""
    import sqlite3, math
    from datetime import date, datetime

    SOURCE_WEIGHTS = {
        "财新网": 1.0, "证券时报": 1.0, "上海证券报": 1.0, "中国证券报": 1.0,
        "证券日报": 1.0, "经济日报": 1.0, "21世纪经济报道": 0.9, "第一财经": 0.9,
        "界面新闻": 0.8, "东方财富": 0.7, "同花顺": 0.7, "新浪财经": 0.6,
        "网易财经": 0.6, "凤凰财经": 0.6, "腾讯财经": 0.6,
    }

    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT source, pub_date, sentiment_score, sentiment_label
        FROM stock_news WHERE stock_id=? AND sentiment_score IS NOT NULL
        ORDER BY pub_date DESC LIMIT 30
    """, (stock_id,)).fetchall()
    conn.close()

    if not rows: return {"score": DEFAULT_SCORE, "count": 0, "method": "default"}

    today = date.today()
    weighted_sum, total_weight = 0, 0
    for r in rows:
        score = r["sentiment_score"]
        source_w = 0.6  # default
        for k, w in SOURCE_WEIGHTS.items():
            if k in (r["source"] or ""): source_w = w; break
        # 时间衰减: 半衰期2天
        try: days_ago = (today - datetime.strptime((r["pub_date"] or "")[:10], "%Y-%m-%d").date()).days
        except: days_ago = 5
        time_w = math.exp(-0.35 * days_ago)
        w = source_w * time_w
        weighted_sum += score * w; total_weight += w

    weighted_score = round(weighted_sum / max(total_weight, 0.01), 1)
    # 低样本平滑：<3条新闻时向50回归
    if len(rows) < 3:
        weighted_score = round(weighted_score * 0.7 + 50 * 0.3, 1)

    return {"score": weighted_score, "count": len(rows), "method": "source_weighted"}


def fill_news_gaps(calc_date: str = None):
    """补全无新闻股票：加权评分 + 同行业龙头代理"""
    import sqlite3, math
    from datetime import date, timedelta
    
    if not calc_date: calc_date = date.today().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    
    # 找出无新闻的股票
    gaps = conn.execute("""
        SELECT s.id as sid, s.code, s.name, s.industry_sw
        FROM stocks s
        WHERE s.is_active=1 AND (
            SELECT COUNT(*) FROM stock_news n WHERE n.stock_id=s.id AND n.sentiment_score IS NOT NULL
        ) = 0
    """).fetchall()
    
    filled = []
    for g in gaps:
        sid, code, ind = g["sid"], g["code"], g["industry_sw"] or ""
        
        # 1. 先用自身的 weighted_news_score
        ws = compute_weighted_news_score(sid)
        if ws["count"] > 0:
            score = ws["score"]
            method = "self_weighted"
        else:
            # 2. 找同行业有新闻最多的股票作为代理
            peer = conn.execute("""
                SELECT n.stock_id, COUNT(*) as cnt
                FROM stock_news n JOIN stocks s ON n.stock_id=s.id
                WHERE s.industry_sw=? AND s.is_active=1 AND n.stock_id!=? AND n.sentiment_score IS NOT NULL
                GROUP BY n.stock_id ORDER BY cnt DESC LIMIT 1
            """, (ind, sid)).fetchone()
            
            if peer:
                ps = compute_weighted_news_score(peer["stock_id"])
                # 代理折扣：0.7倍真实 + 0.3倍中性
                score = round(ps["score"] * 0.7 + 50 * 0.3, 1)
                method = f"peer_proxy(id={peer['stock_id']})"
            else:
                score = 50.0
                method = "default"
        
        conn.execute("UPDATE comprehensive_scores SET sentiment_score=? WHERE stock_id=? AND calc_date=?", (score, sid, calc_date))
        filled.append({"code": code, "score": score, "method": method})
    
    conn.commit(); conn.close()
    return {"filled": len(filled), "details": filled}

def refresh_weighted_news_all(calc_date: str = None):
    """对所有有新闻的股票跑 weighted_news_score 更新"""
    import sqlite3
    from datetime import date
    if not calc_date: calc_date = date.today().strftime("%Y-%m-%d")
    
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    stocks = conn.execute("""
        SELECT DISTINCT n.stock_id FROM stock_news n WHERE n.sentiment_score IS NOT NULL
    """).fetchall()
    
    updated = 0
    for s in stocks:
        ws = compute_weighted_news_score(s["stock_id"])
        if ws["count"] > 0:
            conn.execute("UPDATE comprehensive_scores SET sentiment_score=? WHERE stock_id=? AND calc_date=?",
                        (ws["score"], s["stock_id"], calc_date))
            updated += 1
    
    conn.commit(); conn.close()
    return {"type": "weighted_refresh", "updated": updated}
