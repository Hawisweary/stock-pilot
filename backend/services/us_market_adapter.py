"""美股数据适配器 —— Tiingo(主:基本面/质量) + yfinance(主:批量日线)双轨。

全市场美股约 8290 只活跃股。Tiingo 免费额度(月1000只)扛不住全市场日线,故:
- 日线行情:yfinance 批量(免费、可批下载),Tiingo 逐只兜底/校验。
- 基本面(PE/PB/市值):Tiingo fundamentals-daily(高质量),逐只、按需/渐进。
独立于 A 股(stocks)与 ETF,自成 us_* 表,不参与 V5/ETF 打分。
"""
from __future__ import annotations

import csv
import io
import threading
import time
import zipfile
from typing import Any
from urllib.request import Request, urlopen

import config

_TIINGO_BASE = "https://api.tiingo.com"
_US_EXCHANGES = {"NYSE", "NASDAQ", "AMEX", "NYSE ARCA", "BATS"}

_rate_lock = threading.Lock()
_last_tiingo = 0.0


def _throttle_tiingo(min_interval: float = 0.3) -> None:
    global _last_tiingo
    with _rate_lock:
        wait = min_interval - (time.perf_counter() - _last_tiingo)
        if wait > 0:
            time.sleep(wait)
        _last_tiingo = time.perf_counter()


def _tiingo_get(path: str, params: dict | None = None) -> Any:
    if not config.TIINGO_TOKEN:
        raise RuntimeError("TIINGO_TOKEN 未配置(backend/.env)")
    import json
    from urllib.parse import urlencode

    q = dict(params or {})
    q["token"] = config.TIINGO_TOKEN
    url = f"{_TIINGO_BASE}/{path}?{urlencode(q)}"
    _throttle_tiingo()
    req = Request(url, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _f(v) -> float | None:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


# ── 股票池 ───────────────────────────────────────────────
def fetch_us_universe(active_since: str = "2026-08-01") -> list[dict[str, Any]]:
    """Tiingo supported_tickers → 活跃美股(NYSE/NASDAQ/AMEX, USD, Stock)。"""
    url = "https://apimedia.tiingo.com/docs/tiingo/daily/supported_tickers.zip"
    raw = urlopen(url, timeout=90).read()
    z = zipfile.ZipFile(io.BytesIO(raw))
    reader = csv.DictReader(io.TextIOWrapper(z.open(z.namelist()[0]), encoding="utf-8"))
    out = []
    for r in reader:
        if (r.get("exchange") in _US_EXCHANGES and r.get("assetType") == "Stock"
                and r.get("priceCurrency") == "USD"
                and (r.get("endDate") or "") >= active_since):
            out.append({
                "ticker": r["ticker"],
                "exchange": r["exchange"],
                "start_date": r.get("startDate") or None,
                "end_date": r.get("endDate") or None,
            })
    return out


# ── 日线行情(yfinance 批量)────────────────────────────────
def fetch_us_quotes_bulk(tickers: list[str], period: str = "5d") -> list[dict[str, Any]]:
    """yfinance 批量日线(多只一次下载)。返回展平的行列表。period 如 '5d'/'1mo'/'6mo'/'1y'。"""
    import warnings

    import yfinance as yf

    warnings.filterwarnings("ignore")
    if not tickers:
        return []
    df = yf.download(tickers, period=period, progress=False, auto_adjust=False,
                     threads=True, group_by="ticker")
    if df is None or df.empty:
        return []
    rows = []
    single = len(tickers) == 1
    for tk in tickers:
        try:
            sub = df if single else df[tk]
        except (KeyError, TypeError):
            continue
        if sub is None or sub.empty:
            continue
        for dt, r in sub.iterrows():
            close = _f(r.get("Close"))
            if close is None or close != close:  # 拦 None 与 NaN(yfinance 偶返NaN行)
                continue
            rows.append({
                "ticker": tk,
                "trade_date": str(dt.date()),
                "open": _f(r.get("Open")), "high": _f(r.get("High")),
                "low": _f(r.get("Low")), "close": close,
                "adj_close": _f(r.get("Adj Close")), "volume": _f(r.get("Volume")),
            })
    return rows


def fetch_us_quote_tiingo(ticker: str, start_date: str) -> list[dict[str, Any]]:
    """Tiingo 日线兜底(单只)。"""
    data = _tiingo_get(f"tiingo/daily/{ticker}/prices", {"startDate": start_date})
    if not isinstance(data, list):
        return []
    return [{
        "ticker": ticker,
        "trade_date": str(d["date"])[:10],
        "open": _f(d.get("open")), "high": _f(d.get("high")),
        "low": _f(d.get("low")), "close": _f(d.get("close")),
        "adj_close": _f(d.get("adjClose")), "volume": _f(d.get("volume")),
    } for d in data]


# ── 基本面(Tiingo)──────────────────────────────────────
def fetch_us_meta(ticker: str) -> dict[str, Any] | None:
    """Tiingo 元信息:名称/交易所/描述。"""
    d = _tiingo_get(f"tiingo/daily/{ticker}")
    if not isinstance(d, dict):
        return None
    return {"ticker": ticker, "name": d.get("name"), "exchange": d.get("exchangeCode"),
            "description": (d.get("description") or "")[:500]}


def fetch_us_fundamentals_daily(ticker: str) -> dict[str, Any] | None:
    """Tiingo fundamentals-daily 最新一期:市值/PE/PB/EV/PEG。"""
    data = _tiingo_get(f"tiingo/fundamentals/{ticker}/daily")
    if not isinstance(data, list) or not data:
        return None
    d = data[-1]
    return {
        "ticker": ticker, "date": str(d.get("date"))[:10],
        "market_cap": _f(d.get("marketCap")), "enterprise_val": _f(d.get("enterpriseVal")),
        "pe": _f(d.get("peRatio")), "pb": _f(d.get("pbRatio")),
        "peg": _f(d.get("trailingPEG1Y")),
    }


def fetch_us_names() -> dict[str, str]:
    """SEC company_tickers.json 全量 ticker→公司名(一次下载覆盖全市场)。"""
    import json
    from urllib.request import Request, urlopen

    # SEC 要求 User-Agent 含联系方式(邮箱),否则 403
    req = Request("https://www.sec.gov/files/company_tickers.json",
                  headers={"User-Agent": "StockPilot Research admin@stockpilot.app"})
    d = json.load(urlopen(req, timeout=30))
    return {it["ticker"]: it["title"] for it in d.values() if it.get("ticker")}


def fetch_us_profile(ticker: str) -> dict[str, Any] | None:
    """yfinance .info 富字段:行业/分析师目标价评级/52周/质量成长(免费,单只按需)。"""
    import warnings

    import yfinance as yf

    warnings.filterwarnings("ignore")
    try:
        info = yf.Ticker(ticker).info
    except Exception:
        return None
    if not info:
        return None
    return {
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "market_cap": _f(info.get("marketCap")),  # Tiingo 基本面缺时回退用
        "trailing_pe": _f(info.get("trailingPE")),
        "pb": _f(info.get("priceToBook")),
        "beta": _f(info.get("beta")),
        "forward_pe": _f(info.get("forwardPE")),
        "div_yield": _f(info.get("dividendYield")),
        "wk52_high": _f(info.get("fiftyTwoWeekHigh")),
        "wk52_low": _f(info.get("fiftyTwoWeekLow")),
        "target_mean": _f(info.get("targetMeanPrice")),
        "target_high": _f(info.get("targetHighPrice")),
        "target_low": _f(info.get("targetLowPrice")),
        "recommendation": info.get("recommendationKey"),
        "num_analysts": info.get("numberOfAnalystOpinions"),
        "profit_margin": _f(info.get("profitMargins")),
        "roe": _f(info.get("returnOnEquity")),
        "rev_growth": _f(info.get("revenueGrowth")),
        "eps_growth": _f(info.get("earningsGrowth")),
        "eps": _f(info.get("trailingEps")),
        "employees": info.get("fullTimeEmployees"),
    }
