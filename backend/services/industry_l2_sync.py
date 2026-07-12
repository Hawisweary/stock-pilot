"""申万二级 / 东财板块行业归属同步。"""
from __future__ import annotations

import sqlite3
import time

from config import DB_PATH
from services.data_sources import eastmoney_stock_info

# 美股 / 特殊代码兜底
MANUAL_INDUSTRY_SW2: dict[str, str] = {
    "HSAI": "汽车零部件",
}


def _parse_em2016(raw: str) -> str:
    """东财 EM2016：一级-二级-三级 → 取最细一级作 industry_sw2。"""
    parts = [p.strip() for p in (raw or "").split("-") if p.strip()]
    if not parts:
        return ""
    return parts[-1]


def _fetch_us_industry(code: str) -> str:
    try:
        import yfinance as yf

        t = yf.Ticker(code)
        info = t.info or {}
        sector = (info.get("sector") or info.get("industry") or "").strip()
        if sector:
            return sector
    except Exception:
        pass
    return ""


def _fetch_hsf10_industry(code: str) -> str:
    """F10 公司概况 — push2/push2his 断连时仍可用。"""
    from services.http_client import get as http_get

    prefix = "SH" if code.startswith("6") else "SZ"
    url = (
        "https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax"
        f"?code={prefix}{code}"
    )
    r = http_get(url, timeout=15)
    jb = (r.json().get("jbzl") or [{}])[0]
    em2016 = jb.get("EM2016") or ""
    sw2 = _parse_em2016(str(em2016))
    if sw2:
        return sw2
    return (jb.get("BOARD_NAME") or jb.get("INDUSTRYCSRC1") or "").strip()


def _resolve_sw2(code: str, push_industry: str) -> str:
    """ADATA 申万二级优先，否则东财 F10 / push2 板块名。"""
    try:
        from services.adata_adapter import get_industry_sw

        sw = get_industry_sw(code)
        name = (sw.get("industry") or "").strip()
        itype = (sw.get("industry_type") or "").strip()
        if name and itype and "二" in itype:
            return name
        if name and itype and "一" not in itype:
            return name
    except Exception:
        pass
    hsf10 = _fetch_hsf10_industry(code)
    if hsf10:
        return hsf10
    return (push_industry or "").strip()


def sync_industry_l2(stock_ids: list[int] | None = None, *, limit: int = 200) -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    updated = 0
    errors: list[str] = []
    try:
        if stock_ids:
            ph = ",".join("?" * len(stock_ids))
            rows = conn.execute(
                f"SELECT id, code, market FROM stocks WHERE id IN ({ph}) AND is_active=1",
                stock_ids,
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, code, market FROM stocks WHERE is_active=1
                   AND (industry_sw2 IS NULL OR industry_sw2='' OR industry_sw2='-')
                   ORDER BY id LIMIT ?""",
                (limit,),
            ).fetchall()

        for row in rows:
            sid, code = int(row["id"]), row["code"]
            market = (row["market"] if "market" in row.keys() else None) or "A"
            try:
                push_industry = ""
                if str(market).upper() == "US":
                    push_industry = _fetch_us_industry(code)
                else:
                    try:
                        info = eastmoney_stock_info(code)
                        push_industry = info.get("industry", "") or ""
                    except Exception:
                        pass
                sw2 = MANUAL_INDUSTRY_SW2.get(code) or _resolve_sw2(code, push_industry)
                if sw2 and sw2 not in ("-", "—"):
                    conn.execute(
                        "UPDATE stocks SET industry_sw2=? WHERE id=?",
                        (sw2, sid),
                    )
                    updated += 1
            except Exception as e:
                errors.append(f"{code}:{e}")
            time.sleep(0.12)
        conn.commit()
    finally:
        conn.close()

    return {"updated": updated, "total": len(rows), "errors": errors[:10]}
