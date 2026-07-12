"""申万/东财行业板块资金流 + 相对沪深300强度。"""
from __future__ import annotations

import sqlite3
from datetime import date

from config import DB_PATH, latest_trading_date
from services.http_client import get
from services.market_index import fetch_index_kline

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def _sector_rows() -> list[dict]:
    last_err: Exception | None = None
    items: list = []
    for attempt in range(3):
        try:
            r = get(
                "https://push2.eastmoney.com/api/qt/clist/get",
                params={
                    "pn": "1",
                    "pz": "120",
                    "po": "1",
                    "np": "1",
                    "fltt": "2",
                    "invt": "2",
                    "fs": "m:90+t:2",
                    "fields": "f12,f14,f3,f62,f184,f66,f69",
                },
                headers={"User-Agent": UA},
                timeout=15,
            )
            items = (r.json().get("data") or {}).get("diff") or []
            if items:
                break
        except Exception as e:
            last_err = e
    if not items and last_err:
        raise last_err
    rows = []
    for item in items:
        code = str(item.get("f12") or "")
        name = str(item.get("f14") or "")
        if not code or not name:
            continue
        rows.append(
            {
                "sector_code": code,
                "sector_name": name,
                "change_pct": _f(item.get("f3")),
                "net_inflow": _f(item.get("f62")),
                "net_inflow_pct": _f(item.get("f184")),
                "main_net": _f(item.get("f66")),
                "retail_net_pct": _f(item.get("f69")),
            }
        )
    return rows


def _f(v) -> float | None:
    if v is None or v == "" or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _csi300_return_20d() -> float | None:
    k = fetch_index_kline("sh000300", days=30, with_technical=False)
    bars = k.get("kline") or []
    if len(bars) < 21:
        return None
    c0 = bars[-21].get("close")
    c1 = bars[-1].get("close")
    if not c0 or not c1:
        return None
    return (float(c1) - float(c0)) / float(c0) * 100


def sync_sector_fund_flow(trade_date: str | None = None) -> dict:
    today = trade_date or latest_trading_date() or date.today().strftime("%Y-%m-%d")
    sectors = _sector_rows()
    csi_ret = _csi300_return_20d()

    conn = sqlite3.connect(DB_PATH)
    n = 0
    try:
        for s in sectors:
            rs = None
            chg = s.get("change_pct")
            if chg is not None and csi_ret is not None:
                rs = round(chg - csi_ret, 4)
            conn.execute(
                """INSERT OR REPLACE INTO sector_fund_flow_daily
                (sector_code, sector_name, trade_date, net_inflow, net_inflow_pct,
                 change_pct, rs_csi300_20d, source)
                VALUES (?,?,?,?,?,?,?,?)""",
                (
                    s["sector_code"],
                    s["sector_name"],
                    today,
                    s.get("net_inflow"),
                    s.get("net_inflow_pct"),
                    s.get("change_pct"),
                    rs,
                    "eastmoney",
                ),
            )
            n += 1
        conn.commit()
    finally:
        conn.close()

    return {
        "date": today,
        "sectors": n,
        "csi300_ret_20d": csi_ret,
        "source": "eastmoney",
    }


def get_sector_fund_flow(limit: int = 30) -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        latest_date = conn.execute(
            "SELECT MAX(trade_date) AS d FROM sector_fund_flow_daily"
        ).fetchone()
        d = latest_date["d"] if latest_date else None
        if not d:
            return {"date": None, "sectors": []}
        rows = conn.execute(
            """SELECT sector_code, sector_name, net_inflow, net_inflow_pct,
                      change_pct, rs_csi300_20d
               FROM sector_fund_flow_daily WHERE trade_date=?
               ORDER BY net_inflow DESC LIMIT ?""",
            (d, limit),
        ).fetchall()
    finally:
        conn.close()
    return {"date": d, "sectors": [dict(r) for r in rows]}
