"""主力资金流 — 东财 push2delay / datacenter（push2his 常断连）。"""
from __future__ import annotations

import json
import math
import ssl
from typing import Any

import http.client

_UNVERIFIED_CTX = ssl.create_default_context()
_UNVERIFIED_CTX.check_hostname = False
_UNVERIFIED_CTX.verify_mode = ssl.CERT_NONE

_UT = "b2884a393a59ad64002292a3e90d46a5"
_REFERER = "https://data.eastmoney.com/"
_FLOW_PATH = "/api/qt/stock/fflow/daykline/get"
_CLIST_PATH = "/api/qt/clist/get"
_DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_FS_A_SHARE = (
    "m:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,"
    "m:1+t:2+f:!2,m:1+t:23+f:!2,m:0+t:7+f:!2,m:1+t:3+f:!2"
)


def _to_secid(code: str, market: str = "A") -> str:
    if market != "A":
        return f"0.{code}"
    if code.startswith(("6", "9")):
        return f"1.{code}"
    return f"0.{code}"


def _parse_flow_klines(klines: list[str]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for k in klines:
        parts = k.split(",")
        if len(parts) < 2:
            continue
        try:
            main_in = float(parts[1]) if parts[1] not in ("", "-") else 0.0
            if len(parts) >= 6:
                super_large = float(parts[5]) if parts[5] not in ("", "-") else 0.0
            elif len(parts) >= 3:
                super_large = float(parts[2]) if parts[2] not in ("", "-") else 0.0
            else:
                super_large = 0.0
        except ValueError:
            continue
        results.append(
            {
                "date": parts[0][:10],
                "main_net_inflow": main_in,
                "super_large_inflow": super_large,
            }
        )
    return results


def _flow_params(code: str, market: str, limit: int) -> dict[str, Any]:
    return {
        "secid": _to_secid(code, market),
        "klt": 101,
        "lmt": limit,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "ut": _UT,
    }


def _fetch_push2delay_flow(code: str, market: str, limit: int) -> list[dict[str, Any]]:
    from services.http_client import get as http_get

    r = http_get(
        f"https://push2delay.eastmoney.com{_FLOW_PATH}",
        params=_flow_params(code, market, limit),
        timeout=15,
        headers={"Referer": _REFERER},
    )
    klines = (r.json().get("data") or {}).get("klines") or []
    return _parse_flow_klines(klines)


def _fetch_datacenter_snapshot(code: str) -> list[dict[str, Any]]:
    from services.http_client import get as http_get

    params = {
        "reportName": "RPT_DMSK_TS_STOCKNEW",
        "columns": "SECURITY_CODE,TRADE_DATE,PRIME_INFLOW,SUPERDEAL_INFLOW",
        "filter": f'(SECURITY_CODE="{code}")',
        "pageNumber": "1",
        "pageSize": "1",
        "sortColumns": "TRADE_DATE",
        "sortTypes": "-1",
        "source": "WEB",
        "client": "WEB",
    }
    r = http_get(_DATACENTER_URL, params=params, timeout=15)
    payload = r.json()
    rows = (payload.get("result") or {}).get("data") or []
    if not rows:
        return []
    row = rows[0]
    trade_date = str(row.get("TRADE_DATE", ""))[:10]
    if not trade_date:
        return []
    main_in = row.get("PRIME_INFLOW")
    super_in = row.get("SUPERDEAL_INFLOW")
    return [
        {
            "date": trade_date,
            "main_net_inflow": float(main_in) if main_in not in (None, "", "-") else 0.0,
            "super_large_inflow": float(super_in) if super_in not in (None, "", "-") else 0.0,
        }
    ]


def _fetch_push2his_flow(code: str, market: str, limit: int) -> list[dict[str, Any]]:
    from services.http_client import get as http_get

    r = http_get(
        f"https://push2his.eastmoney.com{_FLOW_PATH}",
        params=_flow_params(code, market, limit),
        timeout=15,
        headers={"Referer": _REFERER},
    )
    klines = (r.json().get("data") or {}).get("klines") or []
    return _parse_flow_klines(klines)


def _fetch_direct_ssl(code: str, market: str, limit: int) -> list[dict[str, Any]]:
    secid = _to_secid(code, market)
    path = (
        f"{_FLOW_PATH}?secid={secid}&klt=101&lmt={limit}"
        f"&fields1=f1,f2,f3,f7"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"
        f"&ut={_UT}"
    )
    conn = http.client.HTTPSConnection(
        "push2his.eastmoney.com", 443, timeout=15, context=_UNVERIFIED_CTX
    )
    try:
        conn.request(
            "GET",
            path,
            headers={"User-Agent": "Mozilla/5.0", "Referer": _REFERER},
        )
        resp = conn.getresponse()
        if resp.status != 200:
            return []
        body = resp.read().decode("utf-8", errors="replace")
    finally:
        conn.close()
    data = json.loads(body)
    klines = (data.get("data") or {}).get("klines") or []
    return _parse_flow_klines(klines)


def fetch_margin_data(code: str, market: str = "A", *, limit: int = 60) -> list[dict[str, Any]]:
    """个股主力净流入日序列（元）。"""
    for fetcher in (_fetch_push2delay_flow, _fetch_push2his_flow, _fetch_direct_ssl):
        try:
            rows = fetcher(code, market, limit)
            if rows:
                return rows
        except Exception:
            continue
    try:
        rows = _fetch_datacenter_snapshot(code)
        if rows:
            return rows
    except Exception:
        pass
    return []


def fetch_main_net_5d_map(codes: set[str] | None = None) -> dict[str, float]:
    """全市场 5 日主力净流入（push2delay clist），可按代码子集过滤。"""
    from services.http_client import get as http_get

    params = {
        "fid": "f164",
        "po": "1",
        "pz": "100",
        "pn": "1",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "ut": _UT,
        "fs": _FS_A_SHARE,
        "fields": "f12,f164",
    }
    r = http_get(
        f"https://push2delay.eastmoney.com{_CLIST_PATH}",
        params=params,
        timeout=20,
        headers={"Referer": _REFERER},
    )
    payload = r.json()
    total = int((payload.get("data") or {}).get("total") or 0)
    if total <= 0:
        return {}

    out: dict[str, float] = {}
    pages = math.ceil(total / 100)
    want = set(codes) if codes else None

    for pn in range(1, pages + 1):
        if pn > 1:
            params["pn"] = str(pn)
            r = http_get(
                f"https://push2delay.eastmoney.com{_CLIST_PATH}",
                params=params,
                timeout=20,
                headers={"Referer": _REFERER},
            )
            payload = r.json()
        for item in (payload.get("data") or {}).get("diff") or []:
            code = str(item.get("f12") or "").strip()
            if not code or (want is not None and code not in want):
                continue
            raw = item.get("f164")
            if raw in (None, "", "-"):
                continue
            try:
                out[code] = float(raw)
            except (TypeError, ValueError):
                continue
        if want is not None and len(out) >= len(want):
            break
    return out
