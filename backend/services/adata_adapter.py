"""
ADATA 适配层 — 使用 PyPI 包 adata（不再 vendor 到仓库）
"""
from __future__ import annotations


def get_industry_sw(code: str) -> dict:
    """获取申万行业分类（中文）"""
    try:
        from adata.stock.info.stock_info import StockInfo

        info = StockInfo()
        df = info.get_industry_sw(stock_code=code)
        if df is not None and not df.empty:
            row = df.iloc[0]
            return {
                "industry": str(row.get("industry_name", "")),
                "sw_code": str(row.get("sw_code", "")),
                "industry_type": str(row.get("industry_type", "")),
            }
    except Exception:
        pass
    return {}


def get_concept_boards(code: str) -> list[str]:
    try:
        from adata.stock.info.concept.stock_concept import StockConcept

        sc = StockConcept()
        df = sc.get_concept_code_ths(stock_code=code)
        if df is not None and not df.empty:
            return df["concept_name"].head(5).tolist()
    except Exception:
        pass
    return []


def get_core_finance(code: str, count: int = 8) -> list[dict]:
    try:
        from adata.stock.finance.core import Core

        core = Core()
        df = core.get_core_index(stock_code=code)
        if df is None or df.empty:
            return []
        results = []
        for _, row in df.tail(count).iterrows():
            results.append({
                "date": str(row.get("report_date", row.get("REPORT_DATE", "")))[:10],
                "roe": safe_float(row.get("roe_wtd", row.get("ROE"))),
                "roa": safe_float(row.get("roa_wtd", row.get("ROA"))),
                "gross_margin": safe_float(row.get("gross_margin", row.get("GROSS_PROFIT_RATIO"))),
                "net_margin": safe_float(row.get("net_margin", row.get("NET_PROFIT_RATIO"))),
                "debt_ratio": safe_float(row.get("asset_liab_ratio", row.get("DEBT_RATIO"))),
                "eps": safe_float(row.get("basic_eps", row.get("EPS"))),
                "bvps": safe_float(row.get("net_asset_ps", row.get("BPS"))),
            })
        return results
    except Exception:
        return []


def get_north_flow(days: int = 20) -> list[dict]:
    try:
        from adata.sentiment.north_flow import NorthFlow

        nf = NorthFlow()
        df = nf.get_north_flow_history(count=days)
        if df is None or df.empty:
            return []
        results = []
        for _, row in df.iterrows():
            results.append({
                "date": str(row.get("TRADE_DATE", ""))[:10],
                "net_flow": safe_float(row.get("NET_DEAL_AMOUNT")),
                "buy_amount": safe_float(row.get("BUY_AMOUNT")),
                "sell_amount": safe_float(row.get("SELL_AMOUNT")),
            })
        return results
    except Exception:
        return []


def safe_float(val) -> float | None:
    import math
    try:
        if val is None:
            return None
        f = float(val)
        return None if math.isnan(f) or math.isinf(f) else f
    except (ValueError, TypeError):
        return None
