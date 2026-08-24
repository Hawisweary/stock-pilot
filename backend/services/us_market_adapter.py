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
    # yfinance 单只也可能返回 MultiIndex 列(group_by=ticker),按列层数判断而非个数
    multi = getattr(df.columns, "nlevels", 1) > 1
    for tk in tickers:
        try:
            sub = df[tk] if multi else df
        except (KeyError, TypeError):
            continue
        if sub is None or sub.empty:
            continue
        for dt, r in sub.iterrows():
            close = _f(r.get("Close"))
            if close is None or close != close:  # 拦 None 与 NaN(yfinance 偶返NaN行)
                continue
            o, h, l = _f(r.get("Open")), _f(r.get("High")), _f(r.get("Low"))
            adj_close = _f(r.get("Adj Close"))
            # yfinance 只给 Adj Close;按 adjClose/close 比率把 O/H/L 一并复权
            ratio = (adj_close / close) if (adj_close and close) else None
            rows.append({
                "ticker": tk,
                "trade_date": str(dt.date()),
                "open": o, "high": h, "low": l, "close": close,
                "adj_open": (o * ratio) if (o and ratio) else None,
                "adj_high": (h * ratio) if (h and ratio) else None,
                "adj_low": (l * ratio) if (l and ratio) else None,
                "adj_close": adj_close, "volume": _f(r.get("Volume")),
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
        "adj_open": _f(d.get("adjOpen")), "adj_high": _f(d.get("adjHigh")),
        "adj_low": _f(d.get("adjLow")), "adj_close": _f(d.get("adjClose")),
        "volume": _f(d.get("volume")),
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


def fetch_us_analyst_detail(ticker: str) -> dict[str, Any]:
    """分析师详细数据:目标价(含中位数)+ 评级分布(强买/买/持有/卖/强卖)+ 逐家券商近期动作。

    数据源 yfinance(聚合 Yahoo 的卖方分析师);单只按需。任一子项失败不影响其余。
    """
    import warnings

    import yfinance as yf

    warnings.filterwarnings("ignore")
    out: dict[str, Any] = {"targets": None, "distribution": None, "actions": []}
    try:
        t = yf.Ticker(ticker)
    except Exception:
        return out

    # 目标价 current/high/low/mean/median
    try:
        pt = t.analyst_price_targets
        if isinstance(pt, dict) and pt:
            out["targets"] = {k: _f(v) for k, v in pt.items()
                              if k in ("current", "high", "low", "mean", "median")}
    except Exception:
        pass

    # 评级分布(取最近一期 0m)
    try:
        rec = t.recommendations
        if rec is not None and not rec.empty:
            row = rec.iloc[0].to_dict()
            out["distribution"] = {
                "period": str(row.get("period", "0m")),
                "strong_buy": int(row.get("strongBuy", 0) or 0),
                "buy": int(row.get("buy", 0) or 0),
                "hold": int(row.get("hold", 0) or 0),
                "sell": int(row.get("sell", 0) or 0),
                "strong_sell": int(row.get("strongSell", 0) or 0),
            }
    except Exception:
        pass

    # 逐家券商近期评级/目标价动作(最近 12 条)
    try:
        ud = t.upgrades_downgrades
        if ud is not None and not ud.empty:
            ud = ud.sort_index(ascending=False).head(12)
            acts = []
            for idx, r in ud.iterrows():
                acts.append({
                    "date": str(idx)[:10],
                    "firm": str(r.get("Firm", "") or ""),
                    "action": str(r.get("Action", "") or ""),
                    "to_grade": str(r.get("ToGrade", "") or ""),
                    "from_grade": str(r.get("FromGrade", "") or ""),
                    "target": _f(r.get("currentPriceTarget")),
                    "prior_target": _f(r.get("priorPriceTarget")),
                })
            out["actions"] = acts
    except Exception:
        pass

    return out


def fetch_us_etf_profile(ticker: str) -> dict[str, Any] | None:
    """ETF 富字段(yfinance):类别/家族/规模/费率/回报 + 资产配置/板块权重/前十持仓。"""
    import warnings

    import yfinance as yf

    warnings.filterwarnings("ignore")
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
    except Exception:
        return None
    exp = _f(info.get("netExpenseRatio"))  # 百分数,如 0.18
    prof: dict[str, Any] = {
        "name": info.get("longName") or info.get("shortName"),
        "category": info.get("category"),
        "fund_family": info.get("fundFamily"),
        "aum": _f(info.get("totalAssets")),
        "nav": _f(info.get("navPrice")),
        "yield": _f(info.get("yield")),
        "ytd_return": _f(info.get("ytdReturn")),
        "three_year_return": _f(info.get("threeYearAverageReturn")),
        "beta3": _f(info.get("beta3Year")),
        "expense_ratio": exp,
        "asset_classes": None, "sectors": [], "holdings": [], "overview": None,
    }
    try:
        fd = t.funds_data
        if fd.asset_classes:
            prof["asset_classes"] = {k: _f(v) for k, v in dict(fd.asset_classes).items()}
        if fd.sector_weightings:
            prof["sectors"] = [{"sector": k, "weight": _f(v)}
                               for k, v in fd.sector_weightings.items() if _f(v)]
        h = fd.top_holdings
        if h is not None and not h.empty:
            prof["holdings"] = [{"symbol": str(idx), "name": str(r.get("Name", "") or ""),
                                 "weight": _f(r.get("Holding Percent"))}
                                for idx, r in h.iterrows()]
        if prof["expense_ratio"] is None:
            er = fd.fund_operations
            v = er.loc["Annual Report Expense Ratio", ticker] if ticker in er.columns else None
            prof["expense_ratio"] = _f(v) * 100 if _f(v) is not None else None
        prof["overview"] = (fd.description or "")[:600] or None
    except Exception:  # noqa: BLE001
        pass
    return prof


def fetch_us_financials(ticker: str) -> dict[str, Any]:
    """yfinance 免费财报:年度营收/净利/EPS 趋势 + 财报超预期(近几期)+ 下次财报日。"""
    import warnings

    import yfinance as yf

    warnings.filterwarnings("ignore")
    out: dict[str, Any] = {"annual": [], "earnings": [], "next_earnings": None}
    try:
        t = yf.Ticker(ticker)
    except Exception:
        return out

    def _row(df, names):
        for n in names:
            try:
                if n in df.index:
                    return df.loc[n]
            except Exception:
                pass
        return None

    # 年度:营收 / 净利 / EPS(近4年)
    try:
        inc = t.income_stmt
        if inc is not None and not inc.empty:
            rev = _row(inc, ["Total Revenue", "Operating Revenue"])
            ni = _row(inc, ["Net Income", "Net Income Common Stockholders"])
            eps = _row(inc, ["Diluted EPS", "Basic EPS"])
            cols = list(inc.columns)[:4]
            rows = []
            for c in cols:
                rows.append({
                    "period": str(c.year) if hasattr(c, "year") else str(c)[:4],
                    "revenue": _f(rev.get(c)) if rev is not None else None,
                    "net_income": _f(ni.get(c)) if ni is not None else None,
                    "eps": _f(eps.get(c)) if eps is not None else None,
                })
            out["annual"] = list(reversed(rows))  # 升序
    except Exception:
        pass

    # 财报超预期 + 下次财报日
    try:
        ed = t.earnings_dates
        if ed is not None and not ed.empty:
            ed = ed.sort_index(ascending=False)
            past, future = [], []
            for idx, r in ed.iterrows():
                est = _f(r.get("EPS Estimate"))
                act = _f(r.get("Reported EPS"))
                sur = _f(r.get("Surprise(%)"))
                d = str(idx)[:10]
                if act is None or act != act:  # 未公布 → 未来
                    future.append(d)
                else:
                    past.append({"date": d, "eps_est": est, "eps_actual": act, "surprise_pct": sur})
            out["earnings"] = past[:8]
            if future:
                out["next_earnings"] = min(future)
    except Exception:
        pass
    return out


def fetch_us_estimates(ticker: str) -> dict[str, Any]:
    """前瞻一致预期:本年/明年 EPS·营收预测(avg+增速+分析师数)+ EPS 上下修。"""
    import warnings

    import yfinance as yf

    warnings.filterwarnings("ignore")
    out: dict[str, Any] = {"periods": [], "revisions": None}
    try:
        t = yf.Ticker(ticker)
        ee = t.earnings_estimate
        re = t.revenue_estimate
        rev = t.eps_revisions
    except Exception:
        return out
    labels = {"0y": "本财年", "+1y": "明财年"}
    for key, cn in labels.items():
        try:
            e = ee.loc[key] if ee is not None and key in ee.index else None
            r = re.loc[key] if re is not None and key in re.index else None
            if e is None:
                continue
            out["periods"].append({
                "label": cn,
                "eps_avg": _f(e.get("avg")), "eps_low": _f(e.get("low")), "eps_high": _f(e.get("high")),
                "eps_growth": _f(e.get("growth")), "n": int(e.get("numberOfAnalysts") or 0),
                "rev_avg": _f(r.get("avg")) if r is not None else None,
                "rev_growth": _f(r.get("growth")) if r is not None else None,
            })
        except Exception:
            continue
    try:
        if rev is not None and "0y" in rev.index:
            row = rev.loc["0y"]
            out["revisions"] = {
                "up_30d": int(row.get("upLast30days") or 0),
                "down_30d": int(row.get("downLast30days") or 0),
            }
    except Exception:
        pass
    return out


def fetch_us_holders(ticker: str) -> dict[str, Any]:
    """持股结构:机构/内部人占比 + 机构数 + 前十大机构。"""
    import warnings

    import yfinance as yf

    warnings.filterwarnings("ignore")
    out: dict[str, Any] = {"inst_pct": None, "insider_pct": None, "inst_count": None, "top": []}
    try:
        t = yf.Ticker(ticker)
        mh = t.major_holders
        ih = t.institutional_holders
    except Exception:
        return out
    try:
        if mh is not None and "Value" in mh.columns:
            g = mh["Value"].to_dict()
            out["inst_pct"] = _f(g.get("institutionsPercentHeld"))
            out["insider_pct"] = _f(g.get("insidersPercentHeld"))
            c = g.get("institutionsCount")
            out["inst_count"] = int(c) if c else None
    except Exception:
        pass
    try:
        if ih is not None and not ih.empty:
            for _, r in ih.head(10).iterrows():
                out["top"].append({
                    "holder": str(r.get("Holder", "")), "pct": _f(r.get("pctHeld")),
                    "value": _f(r.get("Value")), "chg": _f(r.get("pctChange")),
                })
    except Exception:
        pass
    return out


def fetch_us_insider(ticker: str) -> dict[str, Any] | None:
    """内部人近6月增减持汇总(高管买卖)。"""
    import warnings

    import yfinance as yf

    warnings.filterwarnings("ignore")
    try:
        ip = yf.Ticker(ticker).insider_purchases
        if ip is None or ip.empty:
            return None
        m = {str(r.iloc[0]): r for _, r in ip.iterrows()}
        def sh(k):
            row = m.get(k)
            return _f(row.iloc[1]) if row is not None else None
        return {"buy_shares": sh("Purchases"), "sell_shares": sh("Sales"),
                "net_shares": sh("Net Shares Purchased (Sold)")}
    except Exception:
        return None


def fetch_us_actions(ticker: str) -> dict[str, Any]:
    """分红 + 拆股历史(近几期)。"""
    import warnings

    import yfinance as yf

    warnings.filterwarnings("ignore")
    out: dict[str, Any] = {"dividends": [], "splits": []}
    try:
        t = yf.Ticker(ticker)
        div = t.dividends
        spl = t.splits
        if div is not None and len(div):
            for dt_, v in list(div.items())[-8:]:
                out["dividends"].append({"date": str(dt_)[:10], "amount": _f(v)})
        if spl is not None and len(spl):
            for dt_, v in list(spl.items())[-5:]:
                out["splits"].append({"date": str(dt_)[:10], "ratio": _f(v)})
    except Exception:
        pass
    return out


def fetch_us_news(ticker: str, n: int = 8) -> list[dict[str, Any]]:
    """个股新闻(标题/来源/链接/时间)。yfinance news 结构多变,防御式解析。"""
    import warnings

    import yfinance as yf

    warnings.filterwarnings("ignore")
    try:
        raw = yf.Ticker(ticker).news or []
    except Exception:
        return []
    out = []
    for it in raw[:n]:
        c = it.get("content", it) if isinstance(it, dict) else {}
        title = c.get("title") or it.get("title")
        if not title:
            continue
        prov = c.get("provider") or {}
        link = (c.get("canonicalUrl") or {}).get("url") or it.get("link") or (c.get("clickThroughUrl") or {}).get("url")
        out.append({
            "title": title,
            "publisher": (prov.get("displayName") if isinstance(prov, dict) else None) or it.get("publisher"),
            "link": link,
            "date": (c.get("pubDate") or c.get("displayTime") or "")[:10],
        })
    return out


def fetch_us_target_history(ticker: str) -> list[dict[str, Any]]:
    """全量历史评级/目标价(带日期),供"分析师准确度"回测用。

    返回 [{date, target(可空), grade}] 升序。target 用于目标价回测,grade 用于评级方向回测。
    """
    import warnings

    import yfinance as yf

    warnings.filterwarnings("ignore")
    try:
        ud = yf.Ticker(ticker).upgrades_downgrades
    except Exception:
        return []
    if ud is None or ud.empty:
        return []
    try:
        ud = ud.sort_index()
    except Exception:
        return []
    out = []
    for idx, r in ud.iterrows():
        out.append({
            "date": str(idx)[:10],
            "target": _f(r.get("currentPriceTarget")),
            "grade": str(r.get("ToGrade", "") or "").strip(),
        })
    return out
