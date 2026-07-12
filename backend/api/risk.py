"""风险控制 API"""
from fastapi import APIRouter, HTTPException, Query
from config import DB_PATH

router = APIRouter(prefix="/api", tags=["risk"])

@router.get("/stocks/{stock_id}/risk")
async def get_stock_risk(stock_id: int):
    """单只股票风险详情"""
    from services.risk_scorer import compute_single_risk
    return compute_single_risk(stock_id)

@router.post("/risk/analyze-all")
async def analyze_all_risk():
    """全量计算风险评分"""
    from services.risk_scorer import compute_all_risk_scores
    count = compute_all_risk_scores()
    return {"status": "done", "stocks_analyzed": count}

@router.get("/risk/filter")
async def risk_filter(min_risk_score: float = Query(30, ge=0, le=100),
                      max_volatility: float = Query(0.40, ge=0, le=2.0),
                      max_drawdown: float = Query(0.20, ge=0, le=1.0)):
    """风控过滤接口：返回符合风控标准的股票"""
    import sqlite3
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    
    rows = conn.execute("""
        SELECT s.code, s.name, cs.composite_score, cs.risk_score,
               cs.max_drawdown_60d, cs.volatility_20d
        FROM stocks s
        JOIN comprehensive_scores cs ON s.id=cs.stock_id
        WHERE s.is_active=1 AND cs.calc_date=(SELECT MAX(calc_date) FROM comprehensive_scores)
        AND cs.risk_score IS NOT NULL
        AND cs.risk_score >= ? AND cs.volatility_20d <= ?
        AND cs.max_drawdown_60d <= ?
        ORDER BY cs.composite_score DESC
    """, (min_risk_score, max_volatility, max_drawdown)).fetchall()
    conn.close()
    
    passed = len(rows)
    violations = []
    for r in rows:
        flags = []
        if r["max_drawdown_60d"] and r["max_drawdown_60d"] > 0.08:
            flags.append(f"回撤{r['max_drawdown_60d']*100:.1f}%")
        if r["volatility_20d"] and r["volatility_20d"] > 0.30:
            flags.append(f"波动{r['volatility_20d']*100:.0f}%")
        
        # 熔断：波动率>40%时评分×0.7
        effective = r["composite_score"] or 50
        if r["volatility_20d"] and r["volatility_20d"] > 0.40:
            effective *= 0.7
        # 回撤熔断：>8%时仓位上限降至2%
        max_position = 0.05 if not (r["max_drawdown_60d"] and r["max_drawdown_60d"] > 0.08) else 0.02
        
        violations.append({
            "code": r["code"], "name": r["name"],
            "composite_score": round(effective, 1),
            "risk_score": round(r["risk_score"], 1) if r["risk_score"] else None,
            "flags": flags,
            "max_position_pct": max_position * 100,
            "effective_weight": round(max_position * 100, 1),
        })
    
    return {
        "filter_params": {"min_risk": min_risk_score, "max_vol": max_volatility, "max_dd": max_drawdown},
        "passed": passed, "total": len(violations),
        "stocks": violations,
        "constraints": {
            "position_cap_individual": "5%",
            "drawdown_breach_position_cap": "2%",
            "volatility_breach_multiplier": "0.7×score",
        }
    }

@router.get("/risk/heatmap")
async def risk_heatmap():
    """风险热力图数据"""
    import sqlite3
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT s.code, s.name, s.industry_sw,
               cs.risk_score, cs.max_drawdown_60d, cs.volatility_20d, cs.composite_score,
               cs.capital_score, cs.mood_score
        FROM stocks s
        JOIN comprehensive_scores cs ON s.id=cs.stock_id
        WHERE s.is_active=1 AND cs.calc_date=(SELECT MAX(calc_date) FROM comprehensive_scores)
    """).fetchall()
    conn.close()
    
    return [{
        "code": r["code"], "name": r["name"], "industry": r["industry_sw"],
        "risk_score": round(r["risk_score"],1) if r["risk_score"] else None,
        "max_drawdown": round(r["max_drawdown_60d"]*100,1) if r["max_drawdown_60d"] else None,
        "volatility": round(r["volatility_20d"]*100,1) if r["volatility_20d"] else None,
        "composite_score": round(r["composite_score"],1) if r["composite_score"] else None,
        "capital_score": round(r["capital_score"],1) if r["capital_score"] else None,
        "mood_score": round(r["mood_score"],1) if r["mood_score"] else None,
    } for r in rows]
