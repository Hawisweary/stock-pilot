"""AKShare 数据适配器 — 补充 Tushare Pro 5000积分拿不到的特色数据。

覆盖：创新高/新低统计、龙虎榜多周期上榜统计、沪深交易所市场总貌。
筹码分布(stock_cyq_em)因本地 py_mini_racer 依赖损坏暂未接入。
"""
from __future__ import annotations

from typing import Any


def _parse_legulegu_date(raw: Any) -> str:
    """乐咕乐股日期字段：新版为 YYYY-MM-DD 字符串，旧版为毫秒时间戳。"""
    if raw is None:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    try:
        import pandas as pd

        ts = float(s)
        if ts > 1e12:
            ts /= 1000.0
        return pd.to_datetime(ts, unit="s").strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return s[:10]


def fetch_new_high_low_stats() -> list[dict[str, Any]]:
    """全市场创20/60/120日新高新低个股数统计（已剔除停牌股，按日）。"""
    import pandas as pd
    import requests
    from akshare.utils.cons import headers

    url = "https://www.legulegu.com/stockdata/member-ship/get-high-low-statistics/all"
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    data_json = r.json()
    df = pd.DataFrame(data_json)
    if df is None or df.empty:
        return []
    if "indexCode" in df.columns:
        del df["indexCode"]
    rows = []
    for _, r in df.iterrows():
        rows.append({
            "trade_date": _parse_legulegu_date(r.get("date")),
            "close": float(r["close"]) if r.get("close") is not None else None,
            "high20": int(r["high20"]) if r.get("high20") is not None else None,
            "low20": int(r["low20"]) if r.get("low20") is not None else None,
            "high60": int(r["high60"]) if r.get("high60") is not None else None,
            "low60": int(r["low60"]) if r.get("low60") is not None else None,
            "high120": int(r["high120"]) if r.get("high120") is not None else None,
            "low120": int(r["low120"]) if r.get("low120") is not None else None,
        })
    return rows


def fetch_lhb_period_stats(period: str) -> list[dict[str, Any]]:
    """个股龙虎榜多周期上榜统计。period: 近一月/近三月/近六月/近一年。"""
    import akshare as ak

    df = ak.stock_lhb_stock_statistic_em(symbol=period)
    if df is None or df.empty:
        return []
    rows = []
    for _, r in df.iterrows():
        rows.append({
            "code": str(r["代码"]),
            "last_lhb_date": str(r["最近上榜日"]),
            "close": float(r["收盘价"]) if r.get("收盘价") is not None else None,
            "change_pct": float(r["涨跌幅"]) if r.get("涨跌幅") is not None else None,
            "lhb_count": int(r["上榜次数"]) if r.get("上榜次数") is not None else None,
            "lhb_net_amount": float(r["龙虎榜净买额"]) if r.get("龙虎榜净买额") is not None else None,
            "lhb_buy_amount": float(r["龙虎榜买入额"]) if r.get("龙虎榜买入额") is not None else None,
            "lhb_sell_amount": float(r["龙虎榜卖出额"]) if r.get("龙虎榜卖出额") is not None else None,
            "inst_buy_count": int(r["买方机构次数"]) if r.get("买方机构次数") is not None else None,
            "inst_sell_count": int(r["卖方机构次数"]) if r.get("卖方机构次数") is not None else None,
            "inst_net_amount": float(r["机构买入净额"]) if r.get("机构买入净额") is not None else None,
            "chg_1m": float(r["近1个月涨跌幅"]) if r.get("近1个月涨跌幅") is not None else None,
            "chg_3m": float(r["近3个月涨跌幅"]) if r.get("近3个月涨跌幅") is not None else None,
            "chg_6m": float(r["近6个月涨跌幅"]) if r.get("近6个月涨跌幅") is not None else None,
            "chg_1y": float(r["近1年涨跌幅"]) if r.get("近1年涨跌幅") is not None else None,
        })
    return rows


def fetch_market_summary() -> list[dict[str, Any]]:
    """沪深交易所市场总貌（上市公司数/总市值/流通市值/平均市盈率/成交额）。"""
    import akshare as ak

    rows: list[dict[str, Any]] = []

    sse = ak.stock_sse_summary()
    if sse is not None and not sse.empty:
        idx = sse.set_index("项目")
        trade_date = str(idx.loc["报告时间"].iloc[0]) if "报告时间" in idx.index else None
        for cat_col in ("股票", "主板", "科创板"):
            if cat_col not in sse.columns:
                continue
            rows.append({
                "trade_date": trade_date,
                "exchange": "SSE",
                "category": cat_col,
                "count": _to_int(idx.loc["上市公司", cat_col]) if "上市公司" in idx.index else None,
                "turnover": None,
                "total_mv": _to_float(idx.loc["总市值", cat_col]) if "总市值" in idx.index else None,
                "circ_mv": _to_float(idx.loc["流通市值", cat_col]) if "流通市值" in idx.index else None,
                "pe_avg": _to_float(idx.loc["平均市盈率", cat_col]) if "平均市盈率" in idx.index else None,
            })

    szse = ak.stock_szse_summary()
    if szse is not None and not szse.empty:
        # stock_szse_summary 原始单位是"元"，stock_sse_summary 已经是"亿元"，
        # 这里统一换算成亿元，避免同一张表两个交易所的 total_mv/circ_mv 量级差 1e8。
        for _, r in szse.iterrows():
            turnover = _to_float(r.get("成交金额"))
            total_mv = _to_float(r.get("总市值"))
            circ_mv = _to_float(r.get("流通市值"))
            rows.append({
                "trade_date": None,  # 按需由调用方补充当日日期
                "exchange": "SZSE",
                "category": str(r["证券类别"]),
                "count": _to_int(r.get("数量")),
                "turnover": round(turnover / 1e8, 2) if turnover is not None else None,
                "total_mv": round(total_mv / 1e8, 2) if total_mv is not None else None,
                "circ_mv": round(circ_mv / 1e8, 2) if circ_mv is not None else None,
                "pe_avg": None,
            })

    return rows


def _to_float(v: Any) -> float | None:
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _to_int(v: Any) -> int | None:
    f = _to_float(v)
    return int(f) if f is not None else None
