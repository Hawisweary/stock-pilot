"""
同行业深度对比：市值分档过滤 + 指标分位 + 相对强弱摘要
"""
from __future__ import annotations

import statistics
from typing import Any

from api_utils import execute_sql


def _percentile(value: float | None, series: list[float]) -> float | None:
    if value is None or not series:
        return None
    s = sorted(series)
    rank = sum(1 for x in s if x <= value)
    return round(rank / len(s) * 100, 1)


def _median(vals: list[float]) -> float | None:
    if not vals:
        return None
    return round(statistics.median(vals), 4)


def build_deep_peer_analysis(stock_id: int, market_cap_band: float = 0.5) -> dict[str, Any]:
    """
    market_cap_band: 保留市值在 [self/(1+band), self*(1+band)] 内的同行，默认 ±50%
    """
    stock = execute_sql(
        "SELECT id, code, name, industry, industry_sw FROM stocks WHERE id=?",
        (stock_id,),
    )
    if not stock:
        return {"error": "股票不存在"}

    s = stock[0]
    sw = (s.get("industry_sw") or s.get("industry") or "").strip()
    if not sw:
        return {"error": "缺少行业分类", "self": {"code": s["code"], "name": s["name"]}}

    peers_raw = execute_sql(
        """
        SELECT s.id, s.code, s.name FROM stocks s
        WHERE s.is_active=1 AND s.id!=?
          AND (s.industry_sw=? OR s.industry=?)
        ORDER BY s.code
        """,
        (stock_id, sw, sw),
    )

    def _load_metrics(sid: int) -> dict[str, Any]:
        ind = execute_sql(
            """
            SELECT roe, gross_margin, net_margin, debt_to_equity
            FROM financial_indicators
            WHERE stock_id=? AND roe IS NOT NULL
            ORDER BY calc_date DESC LIMIT 1
            """,
            (sid,),
        )
        val = execute_sql(
            """
            SELECT pe_ttm, pb, market_cap FROM valuation_snapshots
            WHERE stock_id=? ORDER BY as_of_date DESC LIMIT 1
            """,
            (sid,),
        )
        scores = execute_sql(
            """
            SELECT profitability_score, growth_score, value_score,
                   momentum_score, safety_score, composite_score
            FROM factor_scores WHERE stock_id=?
            ORDER BY calc_date DESC LIMIT 1
            """,
            (sid,),
        )
        v5 = execute_sql(
            """
            SELECT composite_v5 FROM comprehensive_scores
            WHERE stock_id=? AND composite_v5 IS NOT NULL
            ORDER BY calc_date DESC LIMIT 1
            """,
            (sid,),
        )
        growth = execute_sql(
            """
            SELECT revenue, net_profit_parent, period_end_date
            FROM financial_reports WHERE stock_id=?
            ORDER BY period_end_date DESC LIMIT 2
            """,
            (sid,),
        )
        rev_yoy = None
        if len(growth) >= 2 and growth[0].get("revenue") and growth[1].get("revenue"):
            prev, cur = float(growth[1]["revenue"]), float(growth[0]["revenue"])
            if prev:
                rev_yoy = round((cur - prev) / abs(prev) * 100, 2)

        row: dict[str, Any] = {"stock_id": sid}
        if ind:
            row.update(ind[0])
        if val:
            row.update(val[0])
        if scores:
            row.update(scores[0])
        if v5:
            row["composite_v5"] = v5[0].get("composite_v5")
        row["revenue_yoy"] = rev_yoy
        return row

    self_m = _load_metrics(stock_id)
    self_cap = self_m.get("market_cap")
    peer_rows: list[dict] = []

    for p in peers_raw:
        m = _load_metrics(p["id"])
        cap = m.get("market_cap")
        if self_cap and cap:
            lo = float(self_cap) / (1 + market_cap_band)
            hi = float(self_cap) * (1 + market_cap_band)
            if not (lo <= float(cap) <= hi):
                continue
        peer_rows.append(
            {
                "code": p["code"],
                "name": p["name"],
                "is_current": False,
                **m,
            }
        )

    self_row = {
        "code": s["code"],
        "name": s["name"],
        "is_current": True,
        **self_m,
    }

    metrics_keys = [
        ("pe_ttm", "pe"),
        ("roe", "roe"),
        ("gross_margin", "gross_margin"),
        ("composite_v5", "composite_v5"),
        ("growth_score", "growth_score"),
        ("value_score", "value_score"),
    ]

    percentiles: dict[str, float | None] = {}
    all_rows = peer_rows + [self_row]
    for field, label in metrics_keys:
        series = [
            float(r[field])
            for r in all_rows
            if r.get(field) is not None
        ]
        percentiles[label] = _percentile(
            float(self_m[field]) if self_m.get(field) is not None else None,
            series,
        )

    pe_list = [float(r["pe_ttm"]) for r in peer_rows if r.get("pe_ttm")]
    roe_list = [float(r["roe"]) for r in peer_rows if r.get("roe")]

    strengths: list[str] = []
    weaknesses: list[str] = []
    if percentiles.get("composite_v5") is not None:
        if percentiles["composite_v5"] >= 70:
            strengths.append("V5 综合分处于同业前30%")
        elif percentiles["composite_v5"] <= 30:
            weaknesses.append("V5 综合分处于同业后30%")
    if percentiles.get("value_score") is not None and percentiles["value_score"] >= 65:
        strengths.append("估值因子相对同业更具吸引力")
    if percentiles.get("growth_score") is not None and percentiles["growth_score"] <= 35:
        weaknesses.append("成长因子弱于多数同行")

    return {
        "industry": sw,
        "industry_sw": s.get("industry_sw") or "",
        "peer_count": len(peer_rows),
        "market_cap_band": market_cap_band,
        "self": self_row,
        "peers": peer_rows,
        "percentiles": percentiles,
        "stats": {
            "pe_median": _median(pe_list),
            "pe_min": min(pe_list) if pe_list else None,
            "pe_max": max(pe_list) if pe_list else None,
            "roe_median": _median(roe_list),
        },
        "summary": {
            "strengths": strengths,
            "weaknesses": weaknesses,
        },
    }
