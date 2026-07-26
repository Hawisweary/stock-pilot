"""ML 特征缺失值填充与缩尾（P1）/ V5 数据清洗。"""
from __future__ import annotations

import math
import sqlite3
import statistics
from collections import defaultdict
from datetime import date, timedelta
from typing import Any


def is_valid(v: Any) -> bool:
    if v is None:
        return False
    try:
        f = float(v)
        return math.isfinite(f)
    except (TypeError, ValueError):
        return False


def winsorize(v: float, lo: float, hi: float) -> float:
    if not math.isfinite(v):
        return (lo + hi) / 2
    return max(lo, min(hi, v))


def median_or_zero(vals: list[float]) -> float:
    clean = [float(x) for x in vals if is_valid(x)]
    if not clean:
        return 0.0
    return float(statistics.median(clean))


class ImputeTable:
    """行业 / 全局中位数查找表。"""

    def __init__(self) -> None:
        self._acc: dict[tuple[str, str], list[float]] = defaultdict(list)
        self._by_ind: dict[tuple[str, str], float] = {}
        self._global: dict[str, float] = {}

    def add(self, field: str, industry: str, value: float) -> None:
        if not is_valid(value):
            return
        self._acc[(field, industry or "_unknown")].append(float(value))

    def finalize(self) -> None:
        global_acc: dict[str, list[float]] = defaultdict(list)
        for (field, ind), vals in self._acc.items():
            m = median_or_zero(vals)
            self._by_ind[(field, ind)] = m
            global_acc[field].extend(vals)
        self._global = {f: median_or_zero(v) for f, v in global_acc.items()}

    def lookup(self, field: str, industry: str) -> float:
        ind = industry or "_unknown"
        if (field, ind) in self._by_ind:
            return self._by_ind[(field, ind)]
        return self._global.get(field, 0.0)


WINSOR_BOUNDS: dict[str, tuple[float, float]] = {
    "revenue_yoy_q": (-80.0, 200.0),
    "cfo_np": (-5.0, 5.0),
    "debt_ratio": (0.0, 95.0),
    "eps_revision_3m": (-50.0, 50.0),
    "industry_eps_rev": (-30.0, 30.0),
    "pe_ttm": (0.0, 120.0),
    "pb": (0.0, 15.0),
    "dividend_yield": (0.0, 12.0),
    "main_net_5d": (-1e10, 1e10),
    "margin_chg_20": (-80.0, 80.0),
    "macro_bond_10y": (0.5, 6.0),
    "macro_usd_cnh": (5.0, 8.5),
    "forecast_mid": (-100.0, 500.0),
    "earnings_surprise": (-200.0, 200.0),
    "earnings_revision": (-100.0, 100.0),
    "yoy_dedu_np": (-80.0, 200.0),
    "yoy_sales": (-80.0, 200.0),
}


def _load_stock_industries(conn: sqlite3.Connection) -> dict[int, str]:
    industries: dict[int, str] = {}
    for sid, ind in conn.execute(
        "SELECT id, COALESCE(industry_sw2, industry_sw, '') FROM stocks WHERE is_active=1"
    ):
        industries[int(sid)] = str(ind or "")
    return industries


def _impute_table(
    conn: sqlite3.Connection,
    industries: dict[int, str],
    table: str,
    date_field: str,
    fields: list[str],
    days: int = 90,
) -> dict[str, int]:
    """对指定表做最近 N 天的前向填充 + 行业/全局中位数填充。"""
    cutoff = (date.today() - timedelta(days=days)).isoformat()

    date_rows = conn.execute(
        f"SELECT DISTINCT {date_field} FROM {table} WHERE {date_field} >= ? ORDER BY {date_field}",
        (cutoff,),
    ).fetchall()
    dates = [r[0] for r in date_rows]
    if not dates:
        return {}

    field_str = ", ".join(fields)
    sql = f"SELECT stock_id, {date_field}, {field_str} FROM {table} WHERE {date_field} >= ?"
    data: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in conn.execute(sql, (cutoff,)):
        sid = int(row[0])
        dt = row[1]
        data[sid][dt] = {f: row[i + 2] for i, f in enumerate(fields)}

    # 1. 前向填充（单只股票最近可用值），同时收集待写回项
    field_updates: dict[str, list[tuple]] = {f: [] for f in fields}
    fill_counts: dict[str, int] = {f: 0 for f in fields}
    for sid, hist in data.items():
        last: dict[str, float] = {}
        for dt in dates:
            vals = hist.get(dt)
            if vals is None:
                continue
            for f in fields:
                if is_valid(vals.get(f)):
                    last[f] = float(vals[f])
                elif f in last:
                    vals[f] = last[f]
                    field_updates[f].append((last[f], sid, dt))
                    fill_counts[f] += 1

    # 2. 行业/全局中位数填充剩余缺失值
    for dt in dates:
        impute = ImputeTable()
        for sid, hist in data.items():
            vals = hist.get(dt)
            if vals is None:
                continue
            for f in fields:
                if is_valid(vals.get(f)):
                    impute.add(f, industries.get(sid, ""), vals[f])
        impute.finalize()
        for sid, hist in data.items():
            vals = hist.get(dt)
            if vals is None:
                continue
            for f in fields:
                if not is_valid(vals.get(f)):
                    imputed = impute.lookup(f, industries.get(sid, ""))
                    vals[f] = imputed
                    field_updates[f].append((imputed, sid, dt))
                    fill_counts[f] += 1

    # 3. 批量写回
    for f, ups in field_updates.items():
        if ups:
            conn.executemany(
                f"UPDATE {table} SET {f}=? WHERE stock_id=? AND {date_field}=?",
                ups,
            )
    conn.commit()
    return fill_counts


def impute_v5_tables(conn: sqlite3.Connection, days: int = 90) -> dict[str, dict[str, int]]:
    """回填 V5 生产表中的缺失值：估值与基本面指标。

    策略：
    - 估值：先单股前向填充，再用行业/全局中位数兜底。
    - 基本面：直接行业/全局中位数填充（前向填充为辅）。
    """
    industries = _load_stock_industries(conn)
    result: dict[str, dict[str, int]] = {}
    result["valuation_snapshots"] = _impute_table(
        conn,
        industries,
        "valuation_snapshots",
        "as_of_date",
        ["pe_ttm", "pb", "dividend_yield"],
        days,
    )
    result["stock_v5_metrics"] = _impute_table(
        conn,
        industries,
        "stock_v5_metrics",
        "calc_date",
        ["revenue_yoy_q", "cfo_np", "debt_ratio", "quality_tier"],
        days,
    )
    return result
