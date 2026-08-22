"""全球市场总貌:股指/汇率/大宗/美债/加密。数据源 yfinance(一次批量),多周期涨跌。

独立宏观背景板,不参与任何打分。收益率(^TNX 等)其值本身即百分比。
"""
from __future__ import annotations

import datetime as dt
import warnings

GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    ("全球股指", [
        ("^GSPC", "标普500"), ("^IXIC", "纳斯达克"), ("^DJI", "道琼斯"),
        ("^N225", "日经225"), ("^HSI", "恒生"), ("000001.SS", "上证指数"),
        ("^FTSE", "英国FTSE"), ("^GDAXI", "德国DAX"), ("^FCHI", "法国CAC"),
        ("^STOXX50E", "欧洲斯托克50"), ("^KS11", "韩国KOSPI"), ("^BSESN", "印度Sensex"),
    ]),
    ("汇率", [
        ("DX-Y.NYB", "美元指数"), ("EURUSD=X", "欧元/美元"), ("USDJPY=X", "美元/日元"),
        ("USDCNY=X", "美元/人民币"), ("GBPUSD=X", "英镑/美元"),
    ]),
    ("大宗商品", [
        ("GC=F", "黄金"), ("SI=F", "白银"), ("CL=F", "WTI原油"), ("BZ=F", "布伦特原油"),
        ("HG=F", "铜"), ("NG=F", "天然气"),
    ]),
    ("美债收益率", [
        ("^IRX", "13周"), ("^FVX", "5年"), ("^TNX", "10年"), ("^TYX", "30年"),
    ]),
    ("加密货币", [
        ("BTC-USD", "比特币"), ("ETH-USD", "以太坊"),
    ]),
]

# 收益率组:值本身是利率(%),涨跌以 bp 更直观
_YIELD = {"^IRX", "^FVX", "^TNX", "^TYX"}


def _chg(cur: float | None, ref: float | None) -> float | None:
    if cur is None or ref is None or ref == 0:
        return None
    return round((cur / ref - 1) * 100, 2)


def fetch_global_overview() -> dict:
    """一次批量拉全部标的的近1年日线,计算 日/周/月/年初至今 涨跌。"""
    warnings.filterwarnings("ignore")
    import yfinance as yf

    tickers = [t for _, items in GROUPS for t, _ in items]
    df = yf.download(tickers, period="1y", auto_adjust=False,
                     group_by="ticker", threads=True, progress=False)

    def series(tk):
        try:
            s = df[tk]["Close"].dropna()
        except (KeyError, TypeError):
            return None
        return s if len(s) >= 2 else None

    year = dt.date.today().year
    out = []
    as_of = None
    for gname, items in GROUPS:
        rows = []
        for tk, cn in items:
            s = series(tk)
            if s is None:
                rows.append({"ticker": tk, "name": cn, "last": None})
                continue
            last = float(s.iloc[-1])
            d = str(s.index[-1].date())
            if as_of is None or d > as_of:
                as_of = d
            def at_back(n):
                return float(s.iloc[-1 - n]) if len(s) > n else None
            # 年初至今:今年首个交易日
            ytd_ref = None
            ys = s[s.index >= f"{year}-01-01"]
            if len(ys) >= 1:
                ytd_ref = float(ys.iloc[0])
            is_yield = tk in _YIELD
            row = {
                "ticker": tk, "name": cn, "last": round(last, 2),
                "is_yield": is_yield,
                "chg_1d": _chg(last, at_back(1)),
                "chg_1w": _chg(last, at_back(5)),
                "chg_1m": _chg(last, at_back(21)),
                "chg_ytd": _chg(last, ytd_ref),
            }
            if is_yield:  # 收益率额外给 bp 变动(日)
                prev = at_back(1)
                row["bp_1d"] = round((last - prev) * 100, 1) if prev is not None else None
            rows.append(row)
        out.append({"group": gname, "items": rows})
    return {"as_of": as_of, "groups": out, "updated_at": dt.datetime.now().isoformat()}
