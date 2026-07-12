"""东财 push2his 日线扩展字段 — 成交额/换手率/涨跌幅（OHLCV 仍走腾讯）"""
from __future__ import annotations

import json
import ssl
from typing import Any

_UNVERIFIED_CTX = ssl.create_default_context()
_UNVERIFIED_CTX.check_hostname = False
_UNVERIFIED_CTX.verify_mode = ssl.CERT_NONE

_PUSH2HIS = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_REFERER = "https://quote.eastmoney.com/"


def _to_secid(code: str) -> str:
    if code.startswith(("6", "9")):
        return f"1.{code}"
    return f"0.{code}"


def _parse_kline_row(raw: str) -> dict[str, Any] | None:
    parts = raw.split(",")
    if len(parts) < 11:
        return None
    try:
        return {
            "trade_date": parts[0][:10],
            "amount": float(parts[6]) if parts[6] not in ("", "-") else None,
            "turnover": float(parts[10]) if parts[10] not in ("", "-") else None,
            "change_pct": float(parts[8]) if parts[8] not in ("", "-") else None,
        }
    except (TypeError, ValueError):
        return None


def _fetch_push2his_direct(code: str, count: int) -> list[dict[str, Any]]:
    import http.client

    secid = _to_secid(code)
    path = (
        f"/api/qt/stock/kline/get?secid={secid}&klt=101&fqt=1&lmt={count}"
        f"&end=20500101&fields1=f1,f2,f3,f4,f5,f6"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
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
    rows: list[dict[str, Any]] = []
    for raw in data.get("data", {}).get("klines") or []:
        row = _parse_kline_row(raw)
        if row:
            rows.append(row)
    return rows


def _fetch_push2his_http(code: str, count: int) -> list[dict[str, Any]]:
    from services.http_client import get as http_get

    params = {
        "secid": _to_secid(code),
        "klt": 101,
        "fqt": 1,
        "lmt": count,
        "end": "20500101",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    }
    r = http_get(
        _PUSH2HIS,
        params=params,
        timeout=15,
        headers={"Referer": _REFERER},
    )
    data = r.json()
    rows: list[dict[str, Any]] = []
    for raw in data.get("data", {}).get("klines") or []:
        row = _parse_kline_row(raw)
        if row:
            rows.append(row)
    return rows


def _fetch_akshare_extras(code: str, count: int) -> list[dict[str, Any]]:
    from datetime import datetime, timedelta

    from services.akshare_lazy import akshare as _ak

    ak = _ak()
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=int(count * 1.6))).strftime("%Y%m%d")
    df = ak.stock_zh_a_hist(
        symbol=code,
        period="daily",
        start_date=start,
        end_date=end,
        adjust="qfq",
    )
    if df is None or df.empty:
        return []
    rows: list[dict[str, Any]] = []
    for _, r in df.tail(count).iterrows():
        rows.append(
            {
                "trade_date": str(r["日期"])[:10],
                "amount": float(r["成交额"]) if r.get("成交额") is not None else None,
                "turnover": float(r["换手率"]) if r.get("换手率") is not None else None,
                "change_pct": float(r["涨跌幅"]) if r.get("涨跌幅") is not None else None,
            }
        )
    return rows


def fetch_daily_extras(code: str, count: int = 500) -> dict[str, dict[str, Any]]:
    """按交易日返回 {date: {amount, turnover, change_pct}}。"""
    rows: list[dict[str, Any]] = []
    for fetcher in (_fetch_push2his_http, _fetch_push2his_direct, _fetch_akshare_extras):
        try:
            rows = fetcher(code, count)
            if rows:
                break
        except Exception:
            continue
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        dt = row["trade_date"]
        out[dt] = {
            "amount": row.get("amount"),
            "turnover": row.get("turnover"),
            "change_pct": row.get("change_pct"),
        }
    return out
