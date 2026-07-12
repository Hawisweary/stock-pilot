"""东财 datacenter 宏观指标直连 — 替代 akshare macro_china_*。"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from services.http_client import get

_DC = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_COMMON = {
    "pageNumber": "1",
    "pageSize": "50",
    "sortTypes": "-1",
    "source": "WEB",
    "client": "WEB",
    "p": "1",
    "pageNo": "1",
    "pageNum": "1",
}


def _fetch(report_name: str, *, columns: str = "ALL", sort_columns: str = "REPORT_DATE") -> list[dict]:
    params = {
        **_COMMON,
        "reportName": report_name,
        "columns": columns,
        "sortColumns": sort_columns,
    }
    r = get(_DC, params=params, timeout=15)
    data = r.json()
    return (data.get("result") or {}).get("data") or []


def _safe_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_latest_macro() -> dict[str, Any]:
    """拉取最新宏观快照，字段与 macro_indicators 表对齐。"""
    out: dict[str, Any] = {"source": "eastmoney"}

    cpi_rows = _fetch(
        "RPT_ECONOMY_CPI",
        columns="REPORT_DATE,NATIONAL_BASE,NATIONAL_SAME",
    )
    if cpi_rows:
        row = cpi_rows[0]
        out["cpi"] = _safe_float(row.get("NATIONAL_BASE"))
        out["cpi_yoy"] = _safe_float(row.get("NATIONAL_SAME"))

    pmi_rows = _fetch(
        "RPT_ECONOMY_PMI",
        columns="REPORT_DATE,MAKE_INDEX,NMAKE_INDEX",
    )
    if pmi_rows:
        row = pmi_rows[0]
        out["pmi_manufacturing"] = _safe_float(row.get("MAKE_INDEX"))
        out["pmi_services"] = _safe_float(row.get("NMAKE_INDEX"))

    gdp_rows = _fetch(
        "RPT_ECONOMY_GDP",
        columns="REPORT_DATE,DOMESTICL_PRODUCT_BASE,SUM_SAME",
    )
    if gdp_rows:
        row = gdp_rows[0]
        out["gdp"] = _safe_float(row.get("DOMESTICL_PRODUCT_BASE"))
        out["gdp_yoy"] = _safe_float(row.get("SUM_SAME"))

    m2_rows = _fetch(
        "RPT_ECONOMY_CURRENCY_SUPPLY",
        columns="REPORT_DATE,CURRENCY,CURRENCY_SAME",
        sort_columns="REPORT_DATE",
    )
    if m2_rows:
        row = m2_rows[0]
        out["m2"] = _safe_float(row.get("CURRENCY"))
        out["m2_yoy"] = _safe_float(row.get("CURRENCY_SAME"))

    lpr_rows = _fetch(
        "RPTA_WEB_RATE",
        columns="TRADE_DATE,LPR1Y,LPR5Y",
        sort_columns="TRADE_DATE",
    )
    if lpr_rows:
        row = lpr_rows[0]
        out["lpr_1y"] = _safe_float(row.get("LPR1Y"))
        out["lpr_5y"] = _safe_float(row.get("LPR5Y"))

    params = {
        **_COMMON,
        "reportName": "RPT_IMP_INTRESTRATEN",
        "columns": "REPORT_DATE,IR_RATE",
        "sortColumns": "REPORT_DATE",
        "filter": '(MARKET_CODE="001")(CURRENCY_CODE="CNY")(INDICATOR_ID="001")',
    }
    r = get(_DC, params=params, timeout=15)
    shibor_rows = (r.json().get("result") or {}).get("data") or []
    if shibor_rows:
        out["shibor_overnight"] = _safe_float(shibor_rows[0].get("IR_RATE"))

    # 社融代理：新增人民币贷款（东财 RPT_ECONOMY_RMB_LOAN）
    loan_rows = _fetch(
        "RPT_ECONOMY_RMB_LOAN",
        columns="REPORT_DATE,TIME,RMB_LOAN,RMB_LOAN_SAME,RMB_LOAN_SEQUENTIAL",
    )
    if loan_rows:
        row = loan_rows[0]
        out["social_financing"] = _safe_float(row.get("RMB_LOAN"))
        out["social_financing_yoy"] = _safe_float(row.get("RMB_LOAN_SAME"))
        out["social_financing_mom"] = _safe_float(row.get("RMB_LOAN_SEQUENTIAL"))

    fx = _fetch_usd_cnh()
    if fx is not None:
        out["usd_cnh"] = fx

    bond_10y = _fetch_bond_yield_10y()
    if bond_10y is not None:
        out["bond_yield_10y"] = bond_10y

    return out


def _fetch_usd_cnh() -> float | None:
    """美元兑人民币 — 外汇交易中心 JSON 主路径，东财离岸价备用。"""
    try:
        r = get(
            "https://www.chinamoney.com.cn/r/cms/www/chinamoney/data/fx/rfx-sp-quot.json",
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        for row in r.json().get("records") or []:
            if row.get("ccyPair") == "USD/CNY":
                bid = _safe_float(row.get("bidPrc"))
                ask = _safe_float(row.get("askPrc"))
                if bid is not None and ask is not None:
                    return round((bid + ask) / 2, 4)
                return bid or ask
    except Exception:
        pass
    try:
        r = get(
            "https://push2.eastmoney.com/api/qt/stock/get",
            params={"secid": "133.USDCNH", "fields": "f43"},
            timeout=10,
        )
        raw = (r.json().get("data") or {}).get("f43")
        val = _safe_float(raw)
        if val is None:
            return None
        return round(val / 10000, 4) if val > 100 else val
    except Exception:
        return None


def _fetch_bond_yield_10y() -> float | None:
    series = fetch_bond_yield_series(days=45)
    if not series:
        return None
    latest = max(series.keys())
    return series[latest]


def fetch_bond_yield_series(*, days: int = 252) -> dict[str, float]:
    """10Y 国债收益率日序列 {YYYY-MM-DD: yield}。"""
    out: dict[str, float] = {}
    try:
        end = date.today()
        start = end - timedelta(days=max(days, 30) + 30)
        r = get(
            "https://yield.chinabond.com.cn/cbweb-pbc-web/pbc/historyQuery",
            params={
                "startDate": start.strftime("%Y-%m-%d"),
                "endDate": end.strftime("%Y-%m-%d"),
                "gjqx": "0",
                "qxId": "ycqx",
                "locale": "cn_ZH",
            },
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        text = r.text.replace("&nbsp", "")
        blocks = re.findall(
            r"中债国债收益率曲线</td>\s*<td>(\d{4}-\d{2}-\d{2})</td>(.*?)</tr>",
            text,
            flags=re.S,
        )
        for dt, cells_html in blocks:
            cells = [
                c.strip()
                for c in re.findall(r"<td[^>]*>([^<]*)</td>", cells_html)
                if c.strip()
            ]
            if len(cells) < 7:
                continue
            val = _safe_float(cells[6])
            if val is not None:
                out[dt] = val
    except Exception:
        pass

    if not out:
        try:
            params = {
                **_COMMON,
                "reportName": "RPT_BOND_CB_YIELD",
                "columns": "TRADE_DATE,YIELD_10Y",
                "sortColumns": "TRADE_DATE",
                "sortTypes": "-1",
                "pageSize": str(min(days, 500)),
            }
            r = get(_DC, params=params, timeout=15)
            for row in (r.json().get("result") or {}).get("data") or []:
                dt = str(row.get("TRADE_DATE", ""))[:10]
                val = _safe_float(row.get("YIELD_10Y"))
                if dt and val is not None:
                    out[dt] = val
        except Exception:
            pass
    return out


def fetch_usd_cnh_series(*, days: int = 60) -> dict[str, float]:
    """USD/CNH 日序列（东财离岸）。"""
    out: dict[str, float] = {}
    try:
        params = {
            **_COMMON,
            "reportName": "RPT_FX_OFFSHORE",
            "columns": "TRADE_DATE,NEW_PRICE",
            "sortColumns": "TRADE_DATE",
            "sortTypes": "-1",
            "pageSize": str(min(days, 500)),
        }
        r = get(_DC, params=params, timeout=15)
        for row in (r.json().get("result") or {}).get("data") or []:
            dt = str(row.get("TRADE_DATE", ""))[:10]
            val = _safe_float(row.get("NEW_PRICE"))
            if dt and val is not None:
                out[dt] = val
    except Exception:
        pass
    if not out:
        spot = _fetch_usd_cnh()
        if spot is not None:
            out[date.today().strftime("%Y-%m-%d")] = spot
    return out
