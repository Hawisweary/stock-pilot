"""因子中性化 — 行业 + 市值横截面回归残差"""
from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from config import DB_PATH

NEUTRAL_SUFFIX = "_N"


def _log_mcap(val: Optional[float]) -> float:
    if val is None or val <= 0:
        return 0.0
    return math.log(val)


def _load_cross_section(
    conn: sqlite3.Connection,
    factor_id: str,
    calc_date: str,
) -> Tuple[List[int], List[float], List[str], List[float]]:
    """stock_ids, values, industries, log_mcaps"""
    rows = conn.execute(
        """SELECT fv.stock_id, fv.value, COALESCE(s.industry_sw, s.industry, '') AS ind,
                  (SELECT vs.market_cap FROM valuation_snapshots vs
                   WHERE vs.stock_id=s.id AND vs.market_cap IS NOT NULL
                   ORDER BY vs.as_of_date DESC LIMIT 1) AS mcap
           FROM factor_values fv
           JOIN stocks s ON fv.stock_id = s.id
           WHERE fv.factor_id=? AND fv.date=? AND fv.value IS NOT NULL""",
        (factor_id, calc_date),
    ).fetchall()
    sids, vals, inds, mcaps = [], [], [], []
    for sid, val, ind, mcap in rows:
        sids.append(int(sid))
        vals.append(float(val))
        inds.append(str(ind or "未知"))
        mcaps.append(_log_mcap(mcap))
    return sids, vals, inds, mcaps


def _neutralize_ols(
    y: List[float],
    industries: List[str],
    log_mcaps: List[float],
) -> List[float]:
    """简易 OLS：y ~ intercept + log_mcap + industry dummies，返回残差。"""
    n = len(y)
    if n < 8:
        return y

    ind_set = sorted(set(industries))
    if len(ind_set) > 20:
        top = ind_set[:19]
        industries = [ind if ind in top else "其他" for ind in industries]
        ind_set = sorted(set(industries))

    k = 2 + len(ind_set) - 1  # intercept + mcap + dummies (drop first)
    X: List[List[float]] = []
    for i in range(n):
        row = [1.0, log_mcaps[i]]
        for ind in ind_set[1:]:
            row.append(1.0 if industries[i] == ind else 0.0)
        X.append(row)

    if len(X[0]) >= n:
        return _neutralize_demean(y, industries, log_mcaps)

    # Normal equations with ridge
    p = len(X[0])
    xtx = [[0.0] * p for _ in range(p)]
    xty = [0.0] * p
    for i in range(n):
        for a in range(p):
            xty[a] += X[i][a] * y[i]
            for b in range(p):
                xtx[a][b] += X[i][a] * X[i][b]
    for j in range(p):
        xtx[j][j] += 1e-6

    beta = _solve_linear(xtx, xty)
    if beta is None:
        return _neutralize_demean(y, industries, log_mcaps)

    residuals = []
    for i in range(n):
        pred = sum(X[i][j] * beta[j] for j in range(p))
        residuals.append(y[i] - pred)
    return residuals


def _neutralize_demean(
    y: List[float],
    industries: List[str],
    log_mcaps: List[float],
) -> List[float]:
    """Fallback：行业内 demean + 市值一元回归。"""
    ind_sum: Dict[str, float] = defaultdict(float)
    ind_cnt: Dict[str, int] = defaultdict(int)
    for val, ind in zip(y, industries):
        ind_sum[ind] += val
        ind_cnt[ind] += 1
    ind_mean = {k: ind_sum[k] / ind_cnt[k] for k in ind_sum}
    demeaned = [val - ind_mean.get(ind, 0) for val, ind in zip(y, industries)]

    mx = sum(log_mcaps) / len(log_mcaps)
    my = sum(demeaned) / len(demeaned)
    num = sum((log_mcaps[i] - mx) * (demeaned[i] - my) for i in range(len(y)))
    den = sum((log_mcaps[i] - mx) ** 2 for i in range(len(y))) or 1e-9
    b = num / den
    a = my - b * mx
    return [demeaned[i] - (a + b * log_mcaps[i]) for i in range(len(y))]


def _solve_linear(a: List[List[float]], b: List[float]) -> Optional[List[float]]:
    """高斯消元解 Ax=b。"""
    n = len(a)
    aug = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-12:
            return None
        aug[col], aug[pivot] = aug[pivot], aug[col]
        div = aug[col][col]
        for j in range(col, n + 1):
            aug[col][j] /= div
        for row in range(n):
            if row == col:
                continue
            factor = aug[row][col]
            for j in range(col, n + 1):
                aug[row][j] -= factor * aug[col][j]
    return [aug[i][n] for i in range(n)]


def neutralize_factor(
    source_factor_id: str,
    output_factor_id: Optional[str] = None,
    output_name: Optional[str] = None,
    max_dates: Optional[int] = 60,
) -> dict:
    """对历史截面做行业+市值中性化，写入新因子。"""
    from services.factor_factory import _upsert_factor, init_factor_store

    conn = init_factor_store()
    out_id = output_factor_id or f"{source_factor_id}{NEUTRAL_SUFFIX}"
    out_name = output_name or f"{source_factor_id}_neutral"

    conn.execute(
        "INSERT OR IGNORE INTO factor_registry (factor_id, name, category, formula) VALUES (?,?,?,?)",
        (out_id, out_name, "中性化", json.dumps({"source": source_factor_id}, ensure_ascii=False)),
    )

    dates = [
        r[0]
        for r in conn.execute(
            """SELECT DISTINCT date FROM factor_values WHERE factor_id=?
               ORDER BY date DESC LIMIT ?""",
            (source_factor_id, max_dates or 9999),
        ).fetchall()
    ]
    dates = sorted(dates)
    writes = 0
    for dt in dates:
        sids, vals, inds, mcaps = _load_cross_section(conn, source_factor_id, dt)
        if len(sids) < 8:
            continue
        residuals = _neutralize_ols(vals, inds, mcaps)
        for sid, res in zip(sids, residuals):
            _upsert_factor(conn, sid, dt, out_id, res)
            writes += 1

    conn.commit()
    conn.close()
    return {
        "source_factor_id": source_factor_id,
        "output_factor_id": out_id,
        "dates_processed": len(dates),
        "cells_written": writes,
    }


def neutralize_factor_latest(source_factor_id: str) -> dict:
    conn = sqlite3.connect(DB_PATH)
    dt = conn.execute(
        "SELECT MAX(date) FROM factor_values WHERE factor_id=?", (source_factor_id,)
    ).fetchone()[0]
    conn.close()
    if not dt:
        return {"error": "无因子数据"}
    return neutralize_factor(source_factor_id, max_dates=1)
