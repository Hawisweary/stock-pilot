"""批量抓取计划 — incremental / full 双模式，按股生成可跳过步骤。"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

import config


@dataclass
class StockFetchPlan:
    stock_id: int
    mode: str = "incremental"
    fetch_info: bool = True
    fetch_quotes: bool = True
    fetch_financials: bool = True
    fetch_indicators: bool = True
    fetch_valuation: bool = True
    fetch_announcements: bool = True
    skip_factor: bool = False
    batch_commit: bool = False
    quote_max_bars: int | None = None
    quote_incremental: bool = False
    announcement_limit: int = 30
    finance_fast: bool = False
    skipped_steps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "skipped_steps": list(self.skipped_steps),
            "quote_max_bars": self.quote_max_bars,
            "finance_fast": self.finance_fast,
        }


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value)[:19], fmt)
        except ValueError:
            continue
    return None


def _last_success_map(conn: sqlite3.Connection, stock_ids: list[int]) -> dict[int, dict[str, datetime]]:
    if not stock_ids:
        return {}
    placeholders = ",".join("?" * len(stock_ids))
    # data_fetch_log 在缓存库 cache.db（conn 参数保留兼容旧签名，不再使用）
    from database import cache_connect

    cconn = cache_connect()
    try:
        rows = cconn.execute(
            f"""
            SELECT stock_id, data_type, MAX(fetch_time) AS ft
            FROM data_fetch_log
            WHERE stock_id IN ({placeholders}) AND status='success'
            GROUP BY stock_id, data_type
            """,
            stock_ids,
        ).fetchall()
    finally:
        cconn.close()
    out: dict[int, dict[str, datetime]] = {}
    for r in rows:
        sid = int(r[0])
        dt = _parse_dt(r[2])
        if dt:
            out.setdefault(sid, {})[str(r[1])] = dt
    return out


def _stock_meta(conn: sqlite3.Connection, stock_ids: list[int]) -> dict[int, dict]:
    if not stock_ids:
        return {}
    placeholders = ",".join("?" * len(stock_ids))
    rows = conn.execute(
        f"""
        SELECT id, industry, industry_sw,
               (SELECT MAX(trade_date) FROM stock_daily_quotes WHERE stock_id=stocks.id) AS last_quote
        FROM stocks WHERE id IN ({placeholders})
        """,
        stock_ids,
    ).fetchall()
    return {
        int(r[0]): {
            "industry": r[1],
            "industry_sw": r[2],
            "last_quote": r[3],
        }
        for r in rows
    }


def build_plan(
    stock_id: int,
    mode: str,
    *,
    last_success: dict[str, datetime] | None = None,
    meta: dict | None = None,
    circuit_skip_financials: bool = False,
) -> StockFetchPlan:
    """为单只股票生成抓取计划。"""
    mode = (mode or config.FETCH_DEFAULT_MODE).lower()
    if mode not in ("incremental", "full"):
        mode = "incremental"

    last_success = last_success or {}
    meta = meta or {}
    skipped: list[str] = []
    now = datetime.now()
    finance_age_days = config.FINANCE_FULL_MAX_AGE_DAYS

    if mode == "full":
        return StockFetchPlan(
            stock_id=stock_id,
            mode="full",
            skip_factor=config.FETCH_ALL_SKIP_PER_STOCK_FACTOR,
            batch_commit=True,
            quote_max_bars=config.DATA_FETCH_DAYS,
            quote_incremental=False,
            finance_fast=False,
        )

    plan = StockFetchPlan(
        stock_id=stock_id,
        mode="incremental",
        skip_factor=config.FETCH_ALL_SKIP_PER_STOCK_FACTOR,
        batch_commit=True,
        quote_max_bars=config.FETCH_ALL_QUOTE_DAYS,
        quote_incremental=True,
        finance_fast=False,
    )

    if meta.get("industry") and meta.get("industry_sw"):
        plan.fetch_info = False
        skipped.append("info")

    fin_last = last_success.get("financials") or last_success.get("financials_annual")
    if fin_last and (now - fin_last).days < finance_age_days:
        plan.fetch_financials = False
        skipped.append("financials")

    ind_last = last_success.get("indicators")
    if not plan.fetch_financials and ind_last and (now - ind_last).days < finance_age_days:
        plan.fetch_indicators = False
        skipped.append("indicators")

    if circuit_skip_financials:
        plan.fetch_financials = False
        plan.fetch_indicators = False
        if "financials" not in skipped:
            skipped.append("financials_circuit")
        if "indicators" not in skipped:
            skipped.append("indicators_circuit")

    plan.skipped_steps = skipped
    return plan


def build_plans(
    conn: sqlite3.Connection,
    stocks: list[dict],
    mode: str,
    *,
    circuit_skip_financials: bool = False,
) -> dict[int, StockFetchPlan]:
    ids = [int(s["id"]) for s in stocks]
    success_map = _last_success_map(conn, ids)
    meta_map = _stock_meta(conn, ids)
    plans: dict[int, StockFetchPlan] = {}
    for s in stocks:
        sid = int(s["id"])
        plans[sid] = build_plan(
            sid,
            mode,
            last_success=success_map.get(sid, {}),
            meta=meta_map.get(sid, {}),
            circuit_skip_financials=circuit_skip_financials,
        )
    return plans


def quote_bars_for_stock(
    conn: sqlite3.Connection,
    stock_id: int,
    *,
    max_bars: int,
    incremental: bool,
) -> int:
    """增量模式：按缺口估算少拉根数。"""
    if not incremental:
        return max_bars
    row = conn.execute(
        "SELECT MAX(trade_date) FROM stock_daily_quotes WHERE stock_id=?",
        (stock_id,),
    ).fetchone()
    last_date = row[0] if row else None
    if not last_date:
        return max_bars
    try:
        last = datetime.strptime(str(last_date)[:10], "%Y-%m-%d").date()
    except ValueError:
        return max_bars
    gap = (datetime.now().date() - last).days
    if gap <= 0:
        return min(15, max_bars)
    needed = max(int(gap * 5 / 7) + 8, 15)
    return min(needed, max_bars)
