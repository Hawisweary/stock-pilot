"""东财盈利预测同步 — RPT_WEB_RESPREDICT → 个股 EPS + 行业 3 月修正。"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import config
from config import latest_trading_date
from services.http_client import get

DC_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
REPORT_NAME = "RPT_WEB_RESPREDICT"
COLUMNS = "WEB_RESPREDICT"
PAGE_SIZE = 50


def _safe_float(v) -> float | None:
    if v is None or v == "" or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_int(v) -> int | None:
    if v is None or v == "" or v == "-":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def fetch_eps_forecast_page(page: int = 1, page_size: int = PAGE_SIZE) -> dict:
    """拉取一页东财盈利预测。"""
    params = {
        "reportName": REPORT_NAME,
        "columns": COLUMNS,
        "pageNumber": str(page),
        "pageSize": str(page_size),
        "sortTypes": "-1",
        "sortColumns": "RATING_ORG_NUM",
        "source": "WEB",
        "client": "WEB",
        "p": str(page),
        "pageNo": str(page),
        "pageNum": str(page),
    }
    r = get(DC_URL, params=params, timeout=20)
    result = r.json().get("result") or {}
    return {
        "rows": result.get("data") or [],
        "pages": int(result.get("pages") or 1),
        "count": int(result.get("count") or 0),
    }


def fetch_eps_forecast_for_code(code: str) -> dict | None:
    """按股票代码拉取单条盈利预测。"""
    params = {
        "reportName": REPORT_NAME,
        "columns": COLUMNS,
        "pageNumber": "1",
        "pageSize": "5",
        "sortTypes": "-1",
        "sortColumns": "RATING_ORG_NUM",
        "source": "WEB",
        "client": "WEB",
        "filter": f'(SECURITY_CODE="{code}")',
    }
    r = get(DC_URL, params=params, timeout=15)
    rows = (r.json().get("result") or {}).get("data") or []
    return rows[0] if rows else None


def _parse_forecast_row(row: dict) -> dict:
    return {
        "code": str(row.get("SECURITY_CODE") or ""),
        "eps_fy1": _safe_float(row.get("EPS1")),
        "eps_fy2": _safe_float(row.get("EPS2")),
        "eps_fy1_year": _safe_int(row.get("YEAR1")),
        "eps_fy2_year": _safe_int(row.get("YEAR2")),
        "analyst_count": _safe_int(row.get("RATING_ORG_NUM")),
        "rating_buy": _safe_int(row.get("RATING_BUY_NUM")),
        "industry_board": str(row.get("INDUSTRY_BOARD") or ""),
    }


def _revision_pct(current: float | None, past: float | None) -> float | None:
    if current is None or past is None:
        return None
    denom = abs(past) if abs(past) >= 0.01 else 0.01
    return (current - past) / denom * 100


def _tier_from_revision(pct: float | None) -> int | None:
    if pct is None:
        return None
    if pct >= 10:
        return 2
    if pct >= 5:
        return 1
    if pct >= -5:
        return 0
    if pct >= -10:
        return -1
    return -2


def _past_eps_fy2(conn: sqlite3.Connection, stock_id: int, as_of: str, days: int = 90) -> float | None:
    cutoff = (date.fromisoformat(as_of) - timedelta(days=days)).isoformat()
    row = conn.execute(
        """SELECT eps_fy2 FROM stock_eps_forecast
           WHERE stock_id=? AND as_of_date<=? AND eps_fy2 IS NOT NULL
           ORDER BY as_of_date DESC LIMIT 1""",
        (stock_id, cutoff),
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else None


def sync_stock_eps_forecast(
    stock_ids: list[int] | None = None,
    *,
    as_of_date: str | None = None,
    bulk_fetch: bool = True,
) -> dict:
    """同步个股 EPS 预测并计算 3 月修正率。"""
    as_of = as_of_date or latest_trading_date()
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        if stock_ids:
            ph = ",".join("?" * len(stock_ids))
            stocks = conn.execute(
                f"SELECT id, code, industry_sw2 FROM stocks WHERE id IN ({ph}) AND is_active=1",
                stock_ids,
            ).fetchall()
        else:
            stocks = conn.execute(
                "SELECT id, code, industry_sw2 FROM stocks WHERE is_active=1 ORDER BY id"
            ).fetchall()

        code_map = {str(s["code"]): dict(s) for s in stocks}
        forecast_by_code: dict[str, dict] = {}

        if bulk_fetch and len(stocks) > 5:
            page = 1
            total_pages = 1
            while page <= total_pages:
                batch = fetch_eps_forecast_page(page)
                total_pages = batch["pages"]
                for row in batch["rows"]:
                    parsed = _parse_forecast_row(row)
                    code = parsed["code"]
                    if code in code_map:
                        forecast_by_code[code] = parsed
                page += 1
                if len(forecast_by_code) >= len(code_map):
                    break
        else:
            for s in stocks:
                row = fetch_eps_forecast_for_code(s["code"])
                if row:
                    forecast_by_code[s["code"]] = _parse_forecast_row(row)

        synced = 0
        with_revision = 0
        for code, stock in code_map.items():
            fc = forecast_by_code.get(code)
            if not fc or fc.get("eps_fy2") is None:
                continue
            sid = int(stock["id"])
            past_eps = _past_eps_fy2(conn, sid, as_of)
            rev = _revision_pct(fc["eps_fy2"], past_eps)
            conn.execute(
                """INSERT OR REPLACE INTO stock_eps_forecast
                (stock_id, as_of_date, eps_fy1, eps_fy2, eps_fy1_year, eps_fy2_year,
                 analyst_count, rating_buy, industry_board, revision_3m_pct, source)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    sid,
                    as_of,
                    fc.get("eps_fy1"),
                    fc.get("eps_fy2"),
                    fc.get("eps_fy1_year"),
                    fc.get("eps_fy2_year"),
                    fc.get("analyst_count"),
                    fc.get("rating_buy"),
                    fc.get("industry_board"),
                    rev,
                    "eastmoney",
                ),
            )
            synced += 1
            if rev is not None:
                with_revision += 1

        conn.commit()
        return {
            "as_of_date": as_of,
            "stocks": len(stocks),
            "synced": synced,
            "with_revision_3m": with_revision,
        }
    finally:
        conn.close()


def sync_industry_eps_revision(
    *,
    trade_date: str | None = None,
) -> dict:
    """按 industry_sw2 聚合 3 月 EPS 修正并写入档位。"""
    as_of = trade_date or latest_trading_date()
    conn = sqlite3.connect(config.DB_PATH)
    try:
        rows = conn.execute(
            """SELECT s.industry_sw2, f.revision_3m_pct
               FROM stock_eps_forecast f
               JOIN stocks s ON s.id=f.stock_id
               WHERE f.as_of_date=? AND s.is_active=1
                 AND s.industry_sw2 IS NOT NULL AND s.industry_sw2 != ''
                 AND f.revision_3m_pct IS NOT NULL""",
            (as_of,),
        ).fetchall()

        by_ind: dict[str, list[float]] = {}
        for ind, rev in rows:
            if rev is None:
                continue
            by_ind.setdefault(str(ind), []).append(float(rev))

        industries = 0
        for ind, vals in by_ind.items():
            if not vals:
                continue
            avg_rev = sum(vals) / len(vals)
            tier = _tier_from_revision(avg_rev)
            conn.execute(
                """INSERT OR REPLACE INTO industry_eps_revision_daily
                (industry_sw2, trade_date, revision_3m_pct, stock_count, tier, source)
                VALUES (?,?,?,?,?,?)""",
                (ind, as_of, avg_rev, len(vals), tier, "computed"),
            )
            industries += 1

        conn.commit()
        return {"trade_date": as_of, "industries": industries}
    finally:
        conn.close()


def get_industry_eps_revision(
    industry_sw2: str | None = None,
    *,
    limit: int = 30,
) -> list[dict]:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        if industry_sw2:
            rows = conn.execute(
                """SELECT industry_sw2, trade_date, revision_3m_pct, stock_count, tier, source
                   FROM industry_eps_revision_daily
                   WHERE industry_sw2=?
                   ORDER BY trade_date DESC LIMIT ?""",
                (industry_sw2, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT industry_sw2, trade_date, revision_3m_pct, stock_count, tier, source
                   FROM industry_eps_revision_daily
                   ORDER BY trade_date DESC, revision_3m_pct DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_stock_eps_forecast(stock_id: int) -> dict | None:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """SELECT stock_id, as_of_date, eps_fy1, eps_fy2, eps_fy1_year, eps_fy2_year,
                      analyst_count, rating_buy, industry_board, revision_3m_pct, source
               FROM stock_eps_forecast WHERE stock_id=?
               ORDER BY as_of_date DESC LIMIT 1""",
            (stock_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
