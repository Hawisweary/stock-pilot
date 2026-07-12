"""补全 / 归一化 stocks.industry_sw"""
from __future__ import annotations

import sqlite3
from typing import Optional

from config import DB_PATH
from services.industry_normalize import normalize_industry

# 抓取失败时的兜底（申万一级）
MANUAL_INDUSTRY_SW: dict[str, str] = {
    "688507": "计算机",  # 索辰科技
    "301313": "传媒",  # 凡拓数创
    "688802": "电子",  # 沐曦股份
    "300496": "计算机",  # 中科创达
}


def _fetch_industry(code: str) -> Optional[str]:
    try:
        from services.adata_adapter import get_industry_sw

        sw = get_industry_sw(code)
        raw = sw.get("industry") or sw.get("industry_sw") or ""
        if raw:
            return raw
    except Exception:
        pass
    try:
        import yfinance as yf

        t = yf.Ticker(f"{code}.SS" if code.startswith("6") else f"{code}.SZ")
        info = t.info or {}
        return info.get("industry") or info.get("sector") or ""
    except Exception:
        pass
    return None


def backfill_missing_industries() -> dict:
    """补全 industry_sw 为空的自选股"""
    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("PRAGMA busy_timeout=120000")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT id, code, name, industry, industry_sw FROM stocks
           WHERE is_active=1 AND (industry_sw IS NULL OR industry_sw='')"""
    ).fetchall()
    updated = []
    failed = []
    for r in rows:
        code = r["code"]
        if code in MANUAL_INDUSTRY_SW:
            sw = MANUAL_INDUSTRY_SW[code]
            conn.execute(
                "UPDATE stocks SET industry=COALESCE(NULLIF(industry,''), ?), industry_sw=? WHERE id=?",
                (sw, sw, r["id"]),
            )
            updated.append({"code": code, "name": r["name"], "industry_sw": sw})
            continue
        raw = r["industry"] or _fetch_industry(code) or ""
        sw = normalize_industry(raw, conn) if raw else ""
        if not sw and code in MANUAL_INDUSTRY_SW:
            sw = MANUAL_INDUSTRY_SW[code]
        if sw:
            conn.execute(
                "UPDATE stocks SET industry=COALESCE(NULLIF(industry,''), ?), industry_sw=? WHERE id=?",
                (raw or sw, sw, r["id"]),
            )
            updated.append({"code": code, "name": r["name"], "industry_sw": sw})
        else:
            failed.append({"code": code, "name": r["name"]})
    conn.commit()
    conn.close()
    return {"updated": updated, "failed": failed, "count": len(updated)}


def normalize_all_industry_sw() -> dict:
    """将现有 industry_sw（含英文名）统一为申万一级"""
    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("PRAGMA busy_timeout=120000")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, code, name, industry, industry_sw FROM stocks WHERE is_active=1"
    ).fetchall()
    changed = []
    for r in rows:
        raw = r["industry_sw"] or r["industry"] or ""
        if not raw and r["code"] in MANUAL_INDUSTRY_SW:
            new_sw = MANUAL_INDUSTRY_SW[r["code"]]
        else:
            new_sw = normalize_industry(raw, conn) if raw else ""
        if new_sw and new_sw != (r["industry_sw"] or ""):
            conn.execute("UPDATE stocks SET industry_sw=? WHERE id=?", (new_sw, r["id"]))
            changed.append({"code": r["code"], "from": r["industry_sw"], "to": new_sw})
    conn.commit()
    conn.close()
    return {"normalized": len(changed), "changes": changed}
