"""第3-5轮全部服务 — 财务报表 + 每日综述 + 研报 + 舆情 + 交易日志 + 仓位优化 + 导出"""
import sqlite3, json, math, socket
from datetime import date, timedelta

socket.setdefaulttimeout(8)
from config import DB_PATH


# ═══════════════════ 财务报表解析 ═══════════════════

def sync_financials(stock_id: int, code: str) -> dict:
    """同步单只股票财务报表"""
    today = date.today().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS financial_statements (
        id INTEGER PRIMARY KEY AUTOINCREMENT, stock_id INTEGER, report_date TEXT,
        report_type TEXT, revenue REAL, revenue_yoy REAL, net_profit REAL,
        net_profit_yoy REAL, total_assets REAL, total_liabilities REAL,
        roe REAL, roa REAL, gross_margin REAL, net_margin REAL,
        debt_ratio REAL, current_ratio REAL,
        UNIQUE(stock_id, report_date, report_type))""")
    conn.commit()
    results = []
    try:
        import akshare as ak
        df = ak.stock_financial_abstract_ths(symbol=code, indicator="按报告期")
        if df is not None and not df.empty:
            for _, row in df.tail(4).iterrows():
                conn.execute("""INSERT OR REPLACE INTO financial_statements
                    (stock_id, report_date, report_type, revenue, net_profit,
                     total_assets, total_liabilities, roe, gross_margin, net_margin)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (stock_id, str(row.iloc[0])[:10], "季报",
                     float(row.iloc[1]) if len(row)>1 and row.iloc[1] else 0,
                     float(row.iloc[2]) if len(row)>2 and row.iloc[2] else 0,
                     float(row.iloc[3]) if len(row)>3 and row.iloc[3] else 0,
                     float(row.iloc[4]) if len(row)>4 and row.iloc[4] else 0,
                     float(row.iloc[5]) if len(row)>5 and row.iloc[5] else 0,
                     float(row.iloc[6]) if len(row)>6 and row.iloc[6] else 0,
                     float(row.iloc[7]) if len(row)>7 and row.iloc[7] else 0))
                results.append({"date": str(row.iloc[0])[:10]})
    except Exception as e:
        print(f"[财报] {code} 失败: {e}")
    conn.commit(); conn.close()
    return {"stock_id": stock_id, "code": code, "reports_synced": len(results)}


# ═══════════════════ 每日市场综述 ═══════════════════

def generate_daily_review() -> dict:
    """LLM 生成每日市场综述"""
    today = date.today().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row

    # 收集数据
    top5 = conn.execute("""SELECT s.code, s.name, cs.composite_score
        FROM comprehensive_scores cs JOIN stocks s ON cs.stock_id=s.id
        WHERE s.is_active=1 AND cs.calc_date=? ORDER BY cs.composite_score DESC LIMIT 5""", (today,)).fetchall()
    alerts = conn.execute("""SELECT s.code, s.name, '情绪极值' as type, ss.composite_score
        FROM sentiment_scores ss JOIN stocks s ON ss.stock_id=s.id
        WHERE ss.date=? AND (ss.composite_score<30 OR ss.composite_score>70)""", (today,)).fetchall()
    avg = conn.execute("SELECT ROUND(AVG(composite_score),1) FROM comprehensive_scores WHERE calc_date=?", (today,)).fetchone()
    macro = conn.execute("SELECT * FROM macro_indicators ORDER BY date DESC LIMIT 1").fetchone()
    conn.close()

    top5_text = "\n".join(f"- {r['code']} {r['name']} {r['composite_score']}分" for r in top5)
    alerts_text = "\n".join(f"- {r['code']} {r['name']} {r['type']}={r['composite_score']}分" for r in alerts) if alerts else "无预警"
    macro_text = f"PMI:{macro['pmi_manufacturing']} CPI:{macro['cpi_yoy']} LPR:{macro['lpr_1y']}%" if macro else "暂无"

    prompt = f"""根据以下数据生成每日市场综述：

市场概况：34只股票均值{avg[0] if avg else '?'}分
Top5：{top5_text}
预警：{alerts_text}
宏观：{macro_text}

输出纯JSON：
{{"review": "150字昨日复盘", "focus": "100字今日关注", "risks": "80字风险提示", "advice": "100字操作建议"}}"""

    try:
        from services.news_fetcher import chat_completion
        text = chat_completion(prompt, system_prompt="你是A股投研分析师，输出纯JSON。", max_tokens=600, temperature=0.3)
        import re
        start = text.find("{"); end = text.rfind("}") + 1
        review = json.loads(text[start:end]) if 0 <= start < end else {"error": "LLM 格式错误"}
    except Exception as e:
        return {"error": str(e)}

    # 写入
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS daily_reviews (
        date TEXT PRIMARY KEY, review TEXT, focus TEXT, risks TEXT, advice TEXT)""")
    conn.execute("""INSERT OR REPLACE INTO daily_reviews (date, review, focus, risks, advice)
        VALUES (?,?,?,?,?)""", (today, review.get("review",""), review.get("focus",""),
        review.get("risks",""), review.get("advice","")))
    conn.commit(); conn.close()

    return {"date": today, **review}


# ═══════════════════ 舆情热点检测 ═══════════════════

def detect_hotspots() -> dict:
    """检测最近24小时舆情异常"""
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")

    hotspots = conn.execute("""SELECT s.code, s.name, COUNT(*) as cnt,
        AVG(n.sentiment_score) as avg_sentiment,
        MIN(n.sentiment_score) as min_sentiment,
        GROUP_CONCAT(n.title, ' | ') as titles
        FROM stock_news n JOIN stocks s ON n.stock_id=s.id
        WHERE n.pub_date >= ? GROUP BY n.stock_id
        HAVING cnt > 3 AND (avg_sentiment < -0.3 OR min_sentiment < -0.7)
        ORDER BY cnt DESC LIMIT 10""", (yesterday,)).fetchall()
    conn.close()

    return {
        "date": date.today().strftime("%Y-%m-%d"),
        "count": len(hotspots),
        "hotspots": [dict(r) for r in hotspots],
    }


# ═══════════════════ 仓位优化器 ═══════════════════

def optimize_portfolio(stock_codes: list, capital: float = 100000, risk_level: str = "moderate") -> dict:
    """MPT 最优仓位计算（简化版：等波动率逆向加权）"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 获取近60日收益率
    returns_data = {}
    for code in stock_codes:
        rows = conn.execute("""SELECT sdq.close FROM stock_daily_quotes sdq
            JOIN stocks s ON sdq.stock_id=s.id WHERE s.code=?
            AND sdq.close IS NOT NULL AND sdq.trade_date >= DATE('now','-90 days')
            ORDER BY sdq.trade_date""", (code,)).fetchall()
        prices = [r["close"] for r in rows]
        if len(prices) >= 10:
            rets = [(prices[i]-prices[i-1])/prices[i-1] for i in range(1, len(prices))]
            returns_data[code] = {"std": math.sqrt(sum(r**2 for r in rets)/len(rets)) * math.sqrt(252),
                                  "n": len(rets)}
    conn.close()

    if not returns_data:
        return {"error": "数据不足"}

    # 逆向波动率加权：波动率越低，占比越大
    inv_vol = {c: 1/max(d["std"], 0.01) for c, d in returns_data.items()}
    total = sum(inv_vol.values())
    weights = {c: round(w/total*100, 1) for c, w in inv_vol.items()}

    allocations = []
    for code, w in weights.items():
        lots = int(capital * w / 100 / (100 * 10))  # 假设均价10元
        allocations.append({"code": code, "weight": w, "lots": max(1, lots)})

    return {"risk_level": risk_level, "capital": capital, "allocations": allocations}


# ═══════════════════ 数据导出 ═══════════════════

def export_scores_csv() -> str:
    """导出所有评分到CSV"""
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    rows = conn.execute("""SELECT s.code, s.name, s.industry_sw, cs.*
        FROM comprehensive_scores cs JOIN stocks s ON cs.stock_id=s.id
        WHERE cs.calc_date=(SELECT MAX(calc_date) FROM comprehensive_scores)
        ORDER BY cs.composite_score DESC""").fetchall()
    conn.close()

    headers = "code,name,industry,composite,fundamental,technical,news,capital,policy,mood,valuation"
    lines = [headers]
    for r in rows:
        lines.append(f"{r['code']},{r['name']},{r['industry_sw']},{r['composite_score']},"
                     f"{r['fundamental_score']},{r['technical_score']},{r['sentiment_score']},"
                     f"{r['capital_score']},{r['policy_score']},{r['mood_score']},{r['val_score']}")
    return "\n".join(lines)
