"""机构面评分引擎 — 分析师评级 + 盈利预测 + 机构调研"""
import sqlite3, json, socket
from datetime import date

socket.setdefaulttimeout(8)
from config import DB_PATH


def compute_institution_score(stock_id: int, code: str) -> dict:
    """机构面评分（0-100）"""
    today = date.today().strftime("%Y-%m-%d")

    try:
        import akshare as ak
        df = ak.stock_analyst_rank_em()
    except Exception as e:
        return {"error": f"分析师数据获取失败: {e}"}

    if df is None or df.empty:
        return {"error": "无分析师数据"}

    # 获取股票名称
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT name FROM stocks WHERE code=?", (code,))
    r = c.fetchone()
    conn.close()
    if not r:
        return {"error": "股票不存在"}
    stock_name = r[0]

    # 按名称匹配（分析师排名无代码字段，列0=序号，列1=名称）
    name_col = df.columns[1]  # 股票名称
    row = df[df[name_col].astype(str).str.contains(stock_name[:3], na=False)]
    if row.empty and len(stock_name) >= 4:
        row = df[df[name_col].astype(str).str.contains(stock_name[:4], na=False)]
    if row.empty:
        return {"error": f"该股票({stock_name})不在分析师排名中"}

    d = row.iloc[0]

    # ── 子维度1: 评级得分 (40%) ──
    # 第一列是排名（越小越好）
    rank_val = int(d.iloc[0]) if len(d) > 0 else 10000
    if rank_val <= 50:
        rating_score = 85
    elif rank_val <= 200:
        rating_score = 75
    elif rank_val <= 500:
        rating_score = 65
    elif rank_val <= 1000:
        rating_score = 55
    elif rank_val <= 3000:
        rating_score = 45
    else:
        rating_score = 35

    # ── 子维度2: 预测增长 (30%) ──
    # 列5=3个月预测净利润, 列7=12个月预测净利润
    forecast_3 = float(d.iloc[5]) if len(d) > 5 and d.iloc[5] else 0
    forecast_12 = float(d.iloc[7]) if len(d) > 7 and d.iloc[7] else 0
    growth = (forecast_12 - forecast_3) / forecast_3 if forecast_3 > 0 else 0

    growth_score = 50
    if growth > 0.5:
        growth_score = 85
    elif growth > 0.2:
        growth_score = 75
    elif growth > 0.1:
        growth_score = 65
    elif growth > 0:
        growth_score = 55
    elif growth > -0.1:
        growth_score = 45
    else:
        growth_score = 30

    # ── 子维度3: 机构覆盖度 (30%) ──
    analyst_count = int(d.iloc[8]) if len(d) > 8 and d.iloc[8] else 0

    if analyst_count >= 20:
        cover_score = 85
    elif analyst_count >= 10:
        cover_score = 75
    elif analyst_count >= 5:
        cover_score = 60
    elif analyst_count >= 1:
        cover_score = 45
    else:
        cover_score = 30

    # ── 加权 ──
    composite = rating_score * 0.40 + growth_score * 0.30 + cover_score * 0.30
    composite = round(max(0, min(100, composite)), 1)

    # 写入
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS institution_scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT, stock_id INTEGER NOT NULL,
        date TEXT NOT NULL, composite_score REAL, rating_score REAL,
        growth_score REAL, coverage_score REAL, breakdown_json TEXT,
        UNIQUE(stock_id, date))""")
    conn.execute("""INSERT OR REPLACE INTO institution_scores
        (stock_id,date,composite_score,rating_score,growth_score,coverage_score,breakdown_json)
        VALUES (?,?,?,?,?,?,?)""",
        (stock_id, today, composite, round(rating_score, 1),
         round(growth_score, 1), round(cover_score, 1),
         json.dumps({"rank": int(rank_val), "forecast_growth": round(growth*100, 1), "analyst_count": analyst_count})))
    conn.commit()
    conn.close()

    return {
        "stock_id": stock_id, "code": code, "date": today,
        "composite_score": composite,
        "rating_score": round(rating_score, 1),
        "growth_score": round(growth_score, 1),
        "coverage_score": round(cover_score, 1),
        "signals": {"rank": int(rank_val), "forecast_growth_pct": round(growth*100, 1), "analyst_count": analyst_count},
    }
