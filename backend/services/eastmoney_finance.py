"""
东财 F10 财报直连 — 输出与 akshare 三表 DataFrame 同构，供 data_processor 复用。
"""
from __future__ import annotations

import re
import time
from typing import Literal

import pandas as pd

from services.data_processor import normalize_code, to_exchange_code
from services.http_client import get as http_get

_EM_BASE = "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis"
_SHEET_PATHS = {
    "profit": ("lrbDateAjaxNew", "lrbAjaxNew"),
    "balance": ("zcfzbDateAjaxNew", "zcfzbAjaxNew"),
    "cashflow": ("xjllbDateAjaxNew", "xjllbAjaxNew"),
}
_PERIOD_DATE_TYPE = {
    ("profit", "yearly"): "1",
    ("profit", "quarterly"): "2",
    ("balance", "yearly"): "1",
    ("balance", "quarterly"): "0",
    ("cashflow", "yearly"): "1",
    ("cashflow", "quarterly"): "2",
}
_company_type_cache: dict[str, str] = {}


def to_secucode(code: str) -> str:
    """SH600519 → 600519.SH（东财 datacenter SECUCODE）"""
    ex = to_exchange_code(code)
    num = normalize_code(code)
    if ex.startswith("SH"):
        return f"{num}.SH"
    if ex.startswith("SZ"):
        return f"{num}.SZ"
    if ex.startswith("BJ"):
        return f"{num}.BJ"
    return f"{num}.SH"


def _company_type(exchange_code: str) -> str:
    if exchange_code in _company_type_cache:
        return _company_type_cache[exchange_code]
    url = f"{_EM_BASE}/Index"
    r = http_get(url, params={"type": "web", "code": exchange_code.lower()}, timeout=12)
    m = re.search(r'id="hidctype"[^>]*value="(\d+)"', r.text)
    ctype = m.group(1) if m else "4"
    _company_type_cache[exchange_code] = ctype
    return ctype


def _fetch_sheet(
    sheet: Literal["profit", "balance", "cashflow"],
    exchange_code: str,
    period: Literal["yearly", "quarterly"],
) -> pd.DataFrame:
    date_path, data_path = _SHEET_PATHS[sheet]
    report_date_type = _PERIOD_DATE_TYPE[(sheet, period)]
    company_type = _company_type(exchange_code)

    date_url = f"{_EM_BASE}/{date_path}"
    params = {
        "companyType": company_type,
        "reportDateType": report_date_type,
        "code": exchange_code,
    }
    r = http_get(date_url, params=params, timeout=15)
    data_json = r.json()
    if not data_json.get("data"):
        if sheet == "balance" and period == "yearly":
            params["companyType"] = "3"
            r = http_get(date_url, params=params, timeout=15)
            data_json = r.json()
        if not data_json.get("data"):
            return pd.DataFrame()

    temp_df = pd.DataFrame(data_json["data"])
    if temp_df.empty or "REPORT_DATE" not in temp_df.columns:
        return pd.DataFrame()

    temp_df["REPORT_DATE"] = pd.to_datetime(temp_df["REPORT_DATE"], errors="coerce").dt.date
    temp_df["REPORT_DATE"] = temp_df["REPORT_DATE"].astype(str)
    need_date = [d for d in temp_df["REPORT_DATE"].tolist() if d and d != "NaT"]
    if not need_date:
        return pd.DataFrame()

    chunks = [",".join(need_date[i : i + 5]) for i in range(0, len(need_date), 5)]
    big_df = pd.DataFrame()
    ajax_url = f"{_EM_BASE}/{data_path}"
    for dates in chunks:
        ajax_params = {
            "companyType": params["companyType"],
            "reportDateType": report_date_type,
            "reportType": "1",
            "dates": dates,
            "code": exchange_code,
        }
        r2 = http_get(ajax_url, params=ajax_params, timeout=20)
        j2 = r2.json()
        if "data" not in j2 or not j2["data"]:
            continue
        part = pd.DataFrame(j2["data"])
        for col in part.columns:
            if part[col].isnull().all():
                part[col] = pd.to_numeric(part[col], errors="coerce")
        big_df = part if big_df.empty else pd.concat([big_df, part], ignore_index=True)
        time.sleep(0.15)

    return big_df


def fetch_profit_sheet(code: str, period: Literal["yearly", "quarterly"] = "yearly") -> pd.DataFrame:
    return _fetch_sheet("profit", to_exchange_code(code), period)


def fetch_balance_sheet(code: str, period: Literal["yearly", "quarterly"] = "yearly") -> pd.DataFrame:
    return _fetch_sheet("balance", to_exchange_code(code), period)


def fetch_cashflow_sheet(code: str, period: Literal["yearly", "quarterly"] = "yearly") -> pd.DataFrame:
    return _fetch_sheet("cashflow", to_exchange_code(code), period)


def fetch_financial_indicators_em(code: str) -> pd.DataFrame:
    """东财 F10 主要指标 — 列名含 REPORT_DATE，供 transform_financial_indicators。"""
    secu = to_secucode(code)
    url = "https://datacenter.eastmoney.com/securities/api/data/get"
    params = {
        "type": "RPT_F10_FINANCE_MAINFINADATA",
        "sty": "APP_F10_MAINFINADATA",
        "quoteColumns": "",
        "filter": f'(SECUCODE="{secu}")',
        "p": "1",
        "ps": "200",
        "sr": "-1",
        "st": "REPORT_DATE",
        "source": "HSF10",
        "client": "PC",
    }
    r = http_get(url, params=params, timeout=15)
    data = r.json()
    rows = (data.get("result") or {}).get("data") or []
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if "REPORT_DATE" in df.columns and "日期" not in df.columns:
        df = df.rename(columns={"REPORT_DATE": "日期"})
    return df


def fetch_financial_abstract_sina(code: str) -> pd.DataFrame:
    """新浪关键指标宽表 — akshare stock_financial_abstract 同构 fallback。"""
    num = normalize_code(code)
    prefix = "sh" if num.startswith(("6", "9")) else "sz"
    url = "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"
    params = {"paperCode": f"{prefix}{num}", "source": "gjzb", "type": "0", "page": "1", "num": "1000"}
    r = http_get(url, params=params, timeout=15)
    data_json = r.json()
    report_list = data_json.get("result", {}).get("data", {}).get("report_list") or {}
    key_list = list(report_list.keys())
    if not key_list:
        return pd.DataFrame()

    temp_df = pd.DataFrame(report_list[key_list[0]]["data"])
    big_df = temp_df["item_title"]
    for item in key_list:
        temp_df = pd.DataFrame(report_list[item]["data"])
        big_df = pd.concat([big_df, temp_df["item_value"]], axis=1, ignore_index=True)
    big_df.index = big_df.iloc[:, 0]
    big_df = big_df.iloc[:, 1:]

    sections = [
        ("常用指标", "每股指标"),
        ("每股指标", "盈利能力"),
        ("盈利能力", "成长能力"),
        ("成长能力", "收益质量"),
        ("收益质量", "财务风险"),
        ("财务风险", "营运能力"),
        ("营运能力", None),
    ]
    parts = []
    for start, end in sections:
        if end:
            seg = big_df.loc[start:end]
            seg = seg.iloc[1:-1, :]
        else:
            seg = big_df.loc[start:]
            seg = seg.iloc[1:-1, :]
        seg = seg.reset_index(inplace=False)
        seg.insert(0, "选项", start)
        parts.append(seg)

    out = pd.concat(parts, ignore_index=True)
    cols = ["选项", "指标", *key_list]
    out.columns = cols
    for c in out.columns[2:]:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return out
