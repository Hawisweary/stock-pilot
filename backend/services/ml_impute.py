"""ML 特征缺失值填充与缩尾（P1）。"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
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
}
