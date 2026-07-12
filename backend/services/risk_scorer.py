"""风控评分引擎 V1 — 波动率 + 最大回撤 + 流动性 + VaR"""
import sqlite3, math, json
from datetime import date, datetime
from config import DB_PATH, DEFAULT_SCORE
from services.comprehensive_store import upsert_dimension_score

def compute_all_risk_scores(calc_date: str = None):
    """全量计算风险评分，写入 comprehensive_scores"""
    if not calc_date:
        calc_date = date.today().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row

    stocks = conn.execute("SELECT id, code FROM stocks WHERE is_active=1").fetchall()
    results = []
    
    for s in stocks:
        sid = s["id"]
        # 获取近60日收盘价
        rows = conn.execute("""SELECT trade_date, close, volume FROM stock_daily_quotes
            WHERE stock_id=? AND close IS NOT NULL ORDER BY trade_date DESC LIMIT 60""", (sid,)).fetchall()
        if len(rows) < 20: continue
        rows.reverse()
        closes = [r["close"] for r in rows]
        volumes = [r["volume"] or 0 for r in rows]

        # 1. 波动率（年化）
        log_rets = [math.log(closes[i]/closes[i-1]) for i in range(1, len(closes))]
        vol_20d = math.sqrt(sum(r**2 for r in log_rets[-20:]) / 20 * 252) if len(log_rets) >= 20 else 0.3
        if vol_20d < 0.20: vol_score = 100
        elif vol_20d < 0.30: vol_score = 70
        elif vol_20d < 0.40: vol_score = 40
        else: vol_score = 0

        # 2. 最大回撤（60日）
        peak = closes[0]
        max_dd = 0
        for c in closes:
            if c > peak: peak = c
            dd = (peak - c) / max(peak, 0.01)
            if dd > max_dd: max_dd = dd
        if max_dd < 0.10: dd_score = 100
        elif max_dd < 0.20: dd_score = 70
        elif max_dd < 0.30: dd_score = 40
        else: dd_score = 0

        # 3. 流动性风险（日均成交额分位）
        avg_amount = sum(closes[i]*volumes[i] for i in range(min(20, len(closes)))) / min(20, len(closes))
        results.append({
            "stock_id": sid, "vol_score": vol_score, "dd_score": dd_score,
            "avg_amount": avg_amount, "vol_20d": round(vol_20d, 3),
            "max_dd": round(max_dd, 3), "log_rets": log_rets,
        })

    # 4. 流动性分位 + VaR分位（跨股票）
    if not results: conn.close(); return 0
    amounts = sorted([r["avg_amount"] for r in results])
    amt_logs = [math.log(max(a, 1)) for a in amounts]
    N = len(results)
    
    # VaR计算
    for r in results:
        rets = r["log_rets"]
        if len(rets) >= 20:
            var_95 = sorted(rets[-20:])[max(0, int(len(rets[-20:])*0.05))]
        else:
            var_95 = -0.05
        r["var_95"] = var_95
    
    vars_sorted = sorted([r["var_95"] for r in results])
    
    events = []
    for r in results:
        # 流动性得分（log成交额分位）
        liq_pct = sum(1 for a in amt_logs if a <= math.log(max(r["avg_amount"],1))) / N
        liq_score = round(liq_pct * 100, 1)
        
        # VaR得分（尾部风险，高VaR=低分）
        var_pct = sum(1 for v in vars_sorted if v <= r["var_95"]) / N
        var_score = round((1 - var_pct) * 100, 1)
        
        risk = round(r["vol_score"]*0.40 + r["dd_score"]*0.35 + liq_score*0.15 + var_score*0.10, 1)
        upsert_dimension_score(r["stock_id"], "risk_score", risk, calc_date=calc_date)
        conn.execute("""UPDATE comprehensive_scores SET max_drawdown_60d=?, volatility_20d=?
            WHERE stock_id=? AND calc_date=?""",
            (r["max_dd"], r["vol_20d"], r["stock_id"], calc_date))
        
        # 风险事件记录
        flags = []
        if r["vol_20d"] > 0.40:
            flags.append(("volatility_breach", "critical", f"年化波动率{r['vol_20d']*100:.0f}%"))
        elif r["vol_20d"] > 0.30:
            flags.append(("volatility_breach", "warning", f"年化波动率{r['vol_20d']*100:.0f}%"))
        if r["max_dd"] > 0.20:
            flags.append(("drawdown_breach", "critical", f"最大回撤{r['max_dd']*100:.0f}%"))
        elif r["max_dd"] > 0.08:
            flags.append(("drawdown_breach", "warning", f"回撤{r['max_dd']*100:.0f}%超8%阈值"))
        
        for et, sev, det in flags:
            events.append((r["stock_id"], calc_date, et, sev, json.dumps({"detail": det, "risk_score": risk}, ensure_ascii=False)))

    if events:
        conn.executemany("INSERT INTO risk_events (stock_id, calc_date, event_type, severity, detail_json) VALUES (?,?,?,?,?)", events)
    
    conn.commit()
    conn.close()
    return len(results)

def compute_single_risk(stock_id: int) -> dict:
    """单只股票风险评分"""
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    
    rows = conn.execute("""SELECT trade_date, close, volume FROM stock_daily_quotes
        WHERE stock_id=? AND close IS NOT NULL ORDER BY trade_date DESC LIMIT 60""", (stock_id,)).fetchall()
    if len(rows) < 20:
        conn.close(); return {"error": "数据不足", "risk_score": DEFAULT_SCORE}
    rows.reverse()
    closes = [r["close"] for r in rows]

    log_rets = [math.log(closes[i]/closes[i-1]) for i in range(1, len(closes))]
    vol_20d = math.sqrt(sum(r**2 for r in log_rets[-20:]) / 20 * 252)
    
    peak = closes[0]; max_dd = 0
    for c in closes:
        if c > peak: peak = c
        dd = (peak - c) / max(peak, 0.01)
        if dd > max_dd: max_dd = dd

    var_95 = sorted(log_rets[-20:])[max(0, int(len(log_rets[-20:])*0.05))] if len(log_rets) >= 20 else -0.05

    risk_flags = []
    if vol_20d > 0.40: risk_flags.append(f"波动率熔断({vol_20d*100:.0f}%)")
    elif vol_20d > 0.30: risk_flags.append(f"波动率偏高({vol_20d*100:.0f}%)")
    if max_dd > 0.20: risk_flags.append(f"回撤熔断({max_dd*100:.0f}%)")
    elif max_dd > 0.08: risk_flags.append(f"回撤警告({max_dd*100:.1f}%)")
    
    conn.close()
    return {
        "stock_id": stock_id, "risk_score": round(100 - vol_20d*100 - max_dd*50, 1),
        "max_drawdown_60d": round(max_dd, 3), "volatility_20d": round(vol_20d, 3),
        "var_95": round(var_95, 4), "risk_flags": risk_flags,
    }
