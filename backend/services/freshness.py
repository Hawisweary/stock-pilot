"""V2: 数据新鲜度衰减 — 季报过期后降权"""
import sqlite3
from datetime import date, datetime
from config import DB_PATH

def apply_freshness_decay(calc_date: str = None):
    """对 comprehensive_scores 的基本面评分施加时间衰减"""
    if not calc_date:
        calc_date = date.today().strftime("%Y-%m-%d")
    
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    
    # 获取每只股票的最新季报日期
    stocks = conn.execute("""
        SELECT stock_id, MAX(calc_date) as last_calc
        FROM financial_indicators WHERE calc_date IS NOT NULL
        GROUP BY stock_id
    """).fetchall()
    
    updated = 0
    for s in stocks:
        try:
            last_c = datetime.strptime(s["last_calc"][:10], "%Y-%m-%d").date()
            days_since = (date.today() - last_c).days
            # 新鲜度：30天内1.0，之后每周衰减0.9，100天后0.5
            if days_since <= 30:
                freshness = 1.0
            elif days_since <= 100:
                weeks = (days_since - 30) / 7
                freshness = max(0.5, 1.0 * (0.9 ** weeks))
            else:
                freshness = 0.5
            
            if freshness < 1.0:
                # 更新基本面评分（乘以新鲜度但不低于50%原始分）
                conn.execute("""
                    UPDATE comprehensive_scores SET 
                    fundamental_score = ROUND(fundamental_score * ?, 1),
                    breakdown_json = JSON_SET(COALESCE(breakdown_json,'{}'), '$.freshness', ?)
                    WHERE stock_id=? AND calc_date=?
                """, (max(0.5, freshness), round(freshness, 3), s["stock_id"], calc_date))
                updated += 1
        except: pass
    
    # v3.0: composite_score 写入已移除（8维聚合分废弃）。
    # 综合分由 v5_scorer.compute_all_v5_scores 写入 composite_v5。
    conn.commit()
    conn.close()
    return {"updated": updated, "total": len(stocks)}

def get_freshness_index(stock_id: int) -> dict:
    """单只股票的数据新鲜度指数"""
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    r = conn.execute("""
        SELECT MAX(calc_date) as last_c FROM financial_indicators
        WHERE stock_id=? AND calc_date IS NOT NULL
    """, (stock_id,)).fetchone()
    conn.close()
    if not r or not r["last_c"]:
        return {"freshness": 0.5, "days_since": 999}
    last_c = datetime.strptime(r["last_c"][:10], "%Y-%m-%d").date()
    days = (date.today() - last_c).days
    freshness = max(0.5, 1.0 * (0.9 ** max(0, (days-30)/7)))
    return {"freshness": round(freshness, 3), "days_since": days, "last_calc": r["last_c"][:10]}
