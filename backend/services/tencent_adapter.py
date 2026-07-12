"""腾讯财经行情适配器 — 替代 yfinance，HTTP 协议兼容代理"""
import pandas as pd
from datetime import datetime


def fetch_daily_quotes(code: str, market: str = "A", count: int = 2000) -> pd.DataFrame | None:
    """从腾讯财经获取每日行情（前复权）"""
    exchange = _get_exchange(code, market)
    try:
        from services.Ashare import get_price_day_tx
        df = get_price_day_tx(f"{exchange}{code}", count=count)
        if df is None or df.empty:
            return None
        return df
    except Exception:
        return None


def transform_to_db_rows(df: pd.DataFrame, stock_id: int) -> list[dict]:
    """将腾讯API DataFrame转为 stock_daily_quotes 表格式"""
    rows = []
    for idx, row in df.iterrows():
        date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, 'strftime') else str(idx)[:10]
        close = float(row.get("close", 0) or 0)
        rows.append({
            "stock_id": stock_id,
            "trade_date": date_str,
            "open": float(row.get("open", 0) or 0),
            "high": float(row.get("high", 0) or 0),
            "low": float(row.get("low", 0) or 0),
            "close": close,
            "adj_close": close,
            "volume": int(float(row.get("volume", 0) or 0)),
        })
    return rows


def _get_exchange(code: str, market: str) -> str:
    """腾讯API交易所前缀"""
    if market == "HK":
        return "hk"
    elif market == "US":
        return "us"
    # 北交所: 8/4 开头（原新三板精选层）+ 92 开头（2023起新股统一编号）
    if market == "BJ" or code.startswith(("8", "4", "92", "93")):
        return "bj"
    # A股: 0/3开头 → sz, 6开头 → sh
    if code.startswith(("0", "3")):
        return "sz"
    return "sh"
