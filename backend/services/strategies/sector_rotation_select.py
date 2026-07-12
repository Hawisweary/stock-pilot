"""行业轮动选股 — 加仓行业 Top 股 + 减仓行业清单。"""
from __future__ import annotations

import sqlite3
from typing import List, Optional

from services.sector_rotation import compute_sector_rotation_signals
from services.strategy_types import SelectedStock


def select_sector_rotation(
    conn: sqlite3.Connection,
    *,
    top_n: int = 10,
    window_days: int = 5,
    per_sector: int = 2,
    min_score: float = 0.0,
) -> tuple[List[SelectedStock], List[str], List[str], Optional[str]]:
    """
    返回 (买入列表, 应卖出代码, 减仓行业名, error)。
    """
    sig = compute_sector_rotation_signals(window_days=window_days, force=True)
    if sig.get("error"):
        return [], [], [], str(sig["error"])

    add_sectors = sig.get("add") or []
    reduce_sectors = sig.get("reduce") or []
    reduce_inds = [s["industry"] for s in reduce_sectors]

    candidates: list[dict] = []
    for sec in add_sectors[:5]:
        for st in (sec.get("stocks") or [])[: max(1, per_sector)]:
            score = float(st.get("composite_v5") or st.get("return_5d") or 0)
            if score >= min_score:
                candidates.append(
                    {
                        "stock_id": st["stock_id"],
                        "code": st["code"],
                        "name": st.get("name") or "",
                        "score": score,
                        "industry": sec["industry"],
                    }
                )

    if not candidates:
        return [], [], reduce_inds, "无加仓行业候选股"

    candidates.sort(key=lambda x: -float(x["score"]))
    seen: set[str] = set()
    picked: list[SelectedStock] = []
    for c in candidates:
        if c["code"] in seen:
            continue
        seen.add(c["code"])
        picked.append(
            SelectedStock(
                int(c["stock_id"]),
                c["code"],
                c["name"],
                float(c["score"]),
            )
        )
        if len(picked) >= top_n:
            break

    sell_codes: list[str] = []
    if reduce_inds:
        placeholders = ",".join("?" * len(reduce_inds))
        rows = conn.execute(
            f"""SELECT s.code FROM stocks s
                WHERE s.is_active=1 AND s.industry_sw IN ({placeholders})""",
            reduce_inds,
        ).fetchall()
        sell_codes = [r[0] for r in rows]

    return picked, sell_codes, reduce_inds, None


def sector_rotation_day_scores(
    quotes: dict,
    dates: list[str],
    di: int,
    industry_map: dict[str, str],
    *,
    window: int = 5,
    min_score: float = 0.0,
) -> dict[str, float]:
    """回测用：当日加仓行业股票得分（近 window 日涨幅）。"""
    if di < window:
        return {}
    end_dt = dates[di]
    start_dt = dates[di - window]
    stock_rets: dict[str, float] = {}
    for code, series in quotes.items():
        if end_dt not in series or start_dt not in series:
            continue
        c0 = float(series[start_dt].get("close") or 0)
        c1 = float(series[end_dt].get("close") or 0)
        if c0 <= 0:
            continue
        stock_rets[code] = round((c1 / c0 - 1) * 100, 2)

    if not stock_rets:
        return {}

    pool_avg = sum(stock_rets.values()) / len(stock_rets)
    by_ind: dict[str, list[tuple[str, float]]] = {}
    for code, ret in stock_rets.items():
        ind = industry_map.get(code) or ""
        if not ind:
            continue
        by_ind.setdefault(ind, []).append((code, ret))

    add_inds: set[str] = set()
    for ind, items in by_ind.items():
        avg_ret = sum(x[1] for x in items) / len(items)
        if avg_ret - pool_avg >= 1.0:
            add_inds.add(ind)

    out: dict[str, float] = {}
    for ind in add_inds:
        for code, ret in sorted(by_ind.get(ind, []), key=lambda x: -x[1]):
            if ret >= min_score:
                out[code] = ret
    return out
