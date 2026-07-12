from config import DB_PATH
"""投研分析 API — 季度异动 / PE分位 / 同业对比"""
from fastapi import APIRouter, HTTPException
from api_utils import execute_sql

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


@router.get("/{stock_id}/alerts")
def get_quarterly_alerts(stock_id: int):
    """季度异动告警"""
    reports = execute_sql(
        """SELECT period_end_date, report_type,
                  COALESCE(revenue, operating_revenue, 0) as revenue,
                  COALESCE(net_profit_parent, net_profit, 0) as net_profit_parent,
                  COALESCE(gross_profit, 0) as gross_profit,
                  COALESCE(operating_cf, 0) as operating_cf
           FROM financial_reports WHERE stock_id=?
           ORDER BY period_end_date ASC""",
        (stock_id,)
    )
    if not reports:
        return {"alerts": [], "message": "无财报数据"}

    from services.quarterly_alerts import detect_quarterly_alerts
    stock = execute_sql("SELECT industry, industry_sw FROM stocks WHERE id=?", (stock_id,))
    is_fin = False
    if stock:
        inds = [stock[0].get("industry",""), stock[0].get("industry_sw","")]
        is_fin = any("银行" in i or "保险" in i or "证券" in i or "financial" in i.lower() for i in inds if i)
    alerts = detect_quarterly_alerts(reports, is_financial=is_fin)
    return {"alerts": [a.__dict__ for a in alerts], "total": len(alerts)}


@router.get("/{stock_id}/pe-band")
def get_pe_band(stock_id: int):
    """PE(TTM) 历史分位"""
    quotes = execute_sql(
        """SELECT trade_date, close FROM stock_daily_quotes
           WHERE stock_id=? ORDER BY trade_date ASC""",
        (stock_id,)
    )
    indicators = execute_sql(
        """SELECT calc_date, pe_ttm FROM financial_indicators WHERE stock_id=?
           ORDER BY calc_date ASC""",
        (stock_id,)
    )
    snapshots = execute_sql(
        """SELECT as_of_date, pe_ttm FROM valuation_snapshots
           WHERE stock_id=? ORDER BY as_of_date DESC LIMIT 1""",
        (stock_id,)
    )

    if not quotes or not snapshots:
        return {"error": "缺少行情或估值数据"}

    current_pe = float(snapshots[0]["pe_ttm"]) if snapshots and snapshots[0].get("pe_ttm") else None
    if not current_pe:
        return {"error": "缺少当前 PE_TTM"}

    # 合并每日 PE
    pe_values = []
    for ind in indicators:
        if ind.get("pe_ttm"):
            pe_values.append({
                "date": ind["calc_date"][:10],
                "pe": float(ind["pe_ttm"])
            })

    # 计算分位
    if pe_values:
        pe_list = [p["pe"] for p in pe_values if p["pe"] > 0]
        if pe_list:
            import statistics, math
            mean_val = statistics.mean(pe_list)
            stdev_val = statistics.pstdev(pe_list) if len(pe_list) > 1 else 0
            pe_list_sorted = sorted(pe_list)
            rank = sum(1 for p in pe_list_sorted if p <= current_pe)
            pct = round(rank / len(pe_list_sorted) * 100, 1)

            return {
                "current_pe": current_pe,
                "percentile": pct,
                "mean": round(mean_val, 2),
                "stdev": round(stdev_val, 2),
                "upper_band": round(mean_val + stdev_val, 2),
                "lower_band": round(max(mean_val - stdev_val, 0), 2),
                "min_5y": round(min(pe_list_sorted), 2),
                "max_5y": round(max(pe_list_sorted), 2),
                "history": pe_values[-20:]
            }

    return {"current_pe": current_pe, "message": "PE历史数据不足"}


@router.get("/{stock_id}/peers")
def get_peer_comparison(stock_id: int):
    """同业对比 — 同 industry_sw 股票的 PE/ROE/毛利率"""
    stock = execute_sql(
        "SELECT code, name, industry, industry_sw FROM stocks WHERE id=?", (stock_id,)
    )
    if not stock:
        raise HTTPException(status_code=404, detail="股票不存在")
    s = stock[0]
    sw = s.get("industry_sw") or ""

    # 同行列表
    peers = execute_sql(
        """SELECT s.id, s.code, s.name FROM stocks s
           WHERE s.industry_sw=? AND s.is_active=1 AND s.id!=?
           ORDER BY s.code""",
        (sw, stock_id)
    )

    # 拉同行指标 + 因子评分
    peer_data = []
    for p in peers:
        ind = execute_sql(
            """SELECT roe, gross_margin, net_margin FROM financial_indicators
               WHERE stock_id=? AND roe IS NOT NULL
               ORDER BY calc_date DESC LIMIT 1""",
            (p["id"],)
        )
        val = execute_sql(
            """SELECT pe_ttm, pb, market_cap FROM valuation_snapshots
               WHERE stock_id=? ORDER BY as_of_date DESC LIMIT 1""",
            (p["id"],)
        )
        scores = execute_sql(
            """SELECT profitability_score, growth_score, value_score,
                      momentum_score, safety_score, composite_score
               FROM factor_scores WHERE stock_id=?
               ORDER BY calc_date DESC LIMIT 1""",
            (p["id"],)
        )
        if ind and val:
            s_row = scores[0] if scores else {}
            peer_data.append({
                "code": p["code"],
                "name": p["name"],
                "pe": val[0].get("pe_ttm"),
                "pb": val[0].get("pb"),
                "roe": ind[0].get("roe"),
                "gross_margin": ind[0].get("gross_margin"),
                "market_cap": val[0].get("market_cap"),
                "profitability_score": s_row.get("profitability_score"),
                "growth_score": s_row.get("growth_score"),
                "value_score": s_row.get("value_score"),
                "momentum_score": s_row.get("momentum_score"),
                "safety_score": s_row.get("safety_score"),
                "composite_score": s_row.get("composite_score"),
            })

    # 目标股票自身数据
    self_ind = execute_sql(
        "SELECT roe, gross_margin FROM financial_indicators WHERE stock_id=? AND roe IS NOT NULL ORDER BY calc_date DESC LIMIT 1",
        (stock_id,)
    )
    self_val = execute_sql(
        "SELECT pe_ttm, pb, market_cap FROM valuation_snapshots WHERE stock_id=? ORDER BY as_of_date DESC LIMIT 1",
        (stock_id,)
    )
    self_data = {
        "code": s["code"],
        "name": s["name"],
        "pe": self_val[0].get("pe_ttm") if self_val else None,
        "pb": self_val[0].get("pb") if self_val else None,
        "roe": self_ind[0].get("roe") if self_ind else None,
        "gross_margin": self_ind[0].get("gross_margin") if self_ind else None,
        "market_cap": self_val[0].get("market_cap") if self_val else None,
    }
    self_scores = execute_sql(
        """SELECT profitability_score, growth_score, value_score,
                  momentum_score, safety_score, composite_score
           FROM factor_scores WHERE stock_id=?
           ORDER BY calc_date DESC LIMIT 1""",
        (stock_id,)
    )
    if self_scores:
        self_data.update(self_scores[0])

    # 简单统计
    if peer_data:
        pes = [p["pe"] for p in peer_data if p["pe"]]
        roes = [p["roe"] for p in peer_data if p["roe"]]
        return {
            "self": self_data,
            "peers": peer_data,
            "industry": sw,
            "stats": {
                "pe_median": sorted(pes)[len(pes)//2] if pes else None,
                "pe_min": min(pes) if pes else None,
                "pe_max": max(pes) if pes else None,
                "roe_median": sorted(roes)[len(roes)//2] if roes else None,
                "peer_count": len(peer_data)
            }
        }

    return {"self": self_data, "peers": [], "industry": sw, "message": "无同行数据"}


@router.get("/{stock_id}/deep-peers")
def get_deep_peer_comparison(stock_id: int, market_cap_band: float = 0.5):
    """同行业深度对比：市值分档 + 指标分位 + 相对强弱摘要"""
    from services.peer_analysis import build_deep_peer_analysis

    result = build_deep_peer_analysis(stock_id, market_cap_band=market_cap_band)
    if result.get("error") == "股票不存在":
        raise HTTPException(status_code=404, detail=result["error"])
    return result
