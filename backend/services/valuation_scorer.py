"""估值面评分 V3 — 腾讯实时行情分位 + PEG修正 + 亏损PB回退"""
import sqlite3, json, math
from datetime import date
from config import DB_PATH

def _perc_rank(values: list, target: float) -> float:
    if not values: return 0.5
    return sum(1 for v in values if v <= target) / len(values)

def compute_valuation_score(stock_id: int, code: str) -> dict:
    """估值面评分（0-100）：行业PE分位×0.5 + PB分位×0.3 + 股息率×0.1 + PEG×0.1"""
    from services.data_sources import tencent_quote
    today = date.today().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH)

    # 获取行业
    stock = conn.execute("SELECT industry_sw FROM stocks WHERE id=?", (stock_id,)).fetchone()
    industry = (stock and stock[0]) or ""

    # 获取全市场PE/PB用于分位计算（使用腾讯实时行情）
    all_stocks = conn.execute("SELECT id, code, industry_sw FROM stocks WHERE is_active=1").fetchall()
    codes = [s[1] for s in all_stocks]
    quotes = tencent_quote(codes)

    # 构建行业PE/PB列表
    peers_by_ind = {}
    all_pe, all_pb = [], []
    for s in all_stocks:
        ind = s[2] or ""
        q = quotes.get(s[1], {})
        pe, pb = q.get("pe_ttm", 0) or 0, q.get("pb", 0) or 0
        if ind not in peers_by_ind: peers_by_ind[ind] = []
        peers_by_ind[ind].append({"pe": pe, "pb": pb})
        if pe > 0: all_pe.append(pe)
        if pb > 0: all_pb.append(pb)
    all_pe.sort(); all_pb.sort()

    # 当前股票行情
    q = quotes.get(code, {})
    pe, pb, div = q.get("pe_ttm", 0) or 0, q.get("pb", 0) or 0, q.get("dividend_yield", 0) or 0
    is_loss = pe <= 0

    # 行业分位（≥3同业用行业，否则全市场）
    peers = peers_by_ind.get(industry, [])
    pe_list = sorted([p["pe"] for p in peers if p["pe"] > 0])
    pb_list = sorted([p["pb"] for p in peers if p["pb"] > 0])
    use_ind = len(pe_list) >= 3
    pe_src = pe_list if use_ind else all_pe
    pb_src = pb_list if use_ind else all_pb

    # PE评分（亏损→30基准 + PB分位修正）
    if is_loss:
        pe_score = 30
        # 亏损时用PB分位修正：PB低=加10分，PB高=扣10分
        pb_pct_loss = _perc_rank(pb_src, pb)
        pe_score += round((0.5 - pb_pct_loss) * 20, 1)
    else:
        pe_pct = _perc_rank(pe_src, pe)
        if pe_pct < 0.10: pe_score = 95
        elif pe_pct < 0.30: pe_score = 80
        elif pe_pct < 0.70: pe_score = 60
        elif pe_pct < 0.90: pe_score = 40
        else: pe_score = 20

    # PB评分
    pb_pct = _perc_rank(pb_src, pb)
    pb_score = round(100 * (1 - pb_pct), 1)

    # 股息率评分（行业相对）
    div_scores = [p["pb"] for p in peers if p["pb"] > 0]  # 仅用于统计
    div_score = 0
    div_all = [q2.get("dividend_yield", 0) or 0 for q2 in quotes.values()]
    div_pos = [d for d in div_all if d > 0]
    if div_pos and div > 0:
        avg_d = sum(div_pos) / len(div_pos)
        std_d = math.sqrt(sum((d - avg_d)**2 for d in div_pos) / len(div_pos)) if len(div_pos) > 1 else 1
        if div > avg_d + std_d: div_score = 20
        elif div > avg_d: div_score = 10
        elif div < avg_d - std_d: div_score = -10

    # PEG修正（从factor_scores获取）
    peg_score = 0
    try:
        detail = conn.execute(
            "SELECT score_detail_json FROM factor_scores WHERE stock_id=? ORDER BY calc_date DESC LIMIT 1",
            (stock_id,)
        ).fetchone()
        if detail and detail[0]:
            d = json.loads(detail[0])
            growth = d.get("growth", {})
            profit_cagr = growth.get("profit_cagr_3y", 0)
            if profit_cagr and profit_cagr > 0 and pe > 0:
                cagr_pct = profit_cagr  # already decimal
                peg = pe / (cagr_pct * 100) if cagr_pct > 0 else 999
                if peg <= 0.5: peg_score = 10
                elif peg <= 1.0: peg_score = 5
                elif peg <= 1.5: peg_score = 0
                elif peg <= 2.5: peg_score = -3
                else: peg_score = -8
    except: pass

    # 合成
    composite = pe_score * 0.50 + pb_score * 0.30 + div_score * 0.10 + peg_score * 0.10
    composite = round(max(0, min(100, composite)), 1)

    # 持久化
    conn.execute("""CREATE TABLE IF NOT EXISTS valuation_scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT, stock_id INTEGER NOT NULL,
        date TEXT NOT NULL, composite_score REAL, pe_score REAL,
        pb_score REAL, div_score REAL, peg_score REAL, breakdown_json TEXT,
        UNIQUE(stock_id, date))""")
    conn.execute("""INSERT OR REPLACE INTO valuation_scores
        (stock_id,date,composite_score,pe_score,pb_score,div_score,peg_score,breakdown_json)
        VALUES (?,?,?,?,?,?,?,?)""",
        (stock_id, today, composite, round(pe_score, 1), round(pb_score, 1),
         round(div_score, 1), round(peg_score, 1),
         json.dumps({"pe_ttm": pe, "pb": pb, "pe_score": round(pe_score, 2),
                      "pb_score": round(pb_score, 2), "peg_score": round(peg_score, 2),
                      "div": div})))
    conn.commit(); conn.close()

    return {
        "stock_id": stock_id, "code": code, "date": today,
        "composite_score": composite, "pe_score": round(pe_score, 1),
        "pb_score": round(pb_score, 1), "div_score": round(div_score, 1),
        "peg_score": round(peg_score, 1),
    }
