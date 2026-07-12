"""因子正交化 — 对称正交化 (Löwdin) 按日截面"""
from __future__ import annotations

import json
import math
import sqlite3
from typing import Dict, List, Optional

from config import DB_PATH


def _gram_schmidt_columns(matrix: List[List[float]]) -> List[List[float]]:
    """matrix: n_stocks x k_factors，列向量 Gram-Schmidt 正交化。"""
    n = len(matrix)
    if n == 0:
        return matrix
    k = len(matrix[0])
    if k == 0:
        return matrix
    out = [[0.0] * k for _ in range(n)]
    for j in range(k):
        v = [matrix[i][j] for i in range(n)]
        for p in range(j):
            dot = sum(v[i] * out[i][p] for i in range(n))
            norm_p = sum(out[i][p] ** 2 for i in range(n)) or 1e-9
            v = [v[i] - dot / norm_p * out[i][p] for i in range(n)]
        norm_v = math.sqrt(sum(x * x for x in v)) or 1e-9
        for i in range(n):
            out[i][j] = v[i] / norm_v
    return out


def orthogonalize_factors(
    factor_ids: List[str],
    name_prefix: str = "ortho",
    max_dates: Optional[int] = 60,
) -> dict:
    """对多因子按日对称正交化，各输出一个正交因子 F0xx。"""
    from services.factor_factory import _upsert_factor, init_factor_store

    if len(factor_ids) < 2:
        return {"error": "至少 2 个因子"}

    conn = init_factor_store()
    ph = ",".join("?" * len(factor_ids))
    dates = [
        r[0]
        for r in conn.execute(
            f"""SELECT date FROM factor_values WHERE factor_id IN ({ph})
                GROUP BY date HAVING COUNT(DISTINCT factor_id)=?
                ORDER BY date DESC LIMIT ?""",
            (*factor_ids, len(factor_ids), max_dates or 9999),
        ).fetchall()
    ]
    dates = sorted(dates)

    out_ids: List[str] = []
    max_row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(factor_id,2) AS INTEGER)) FROM factor_registry WHERE factor_id LIKE 'F%'"
    ).fetchone()[0]
    next_n = (max_row or 15) + 1
    for i, fid in enumerate(factor_ids):
        out_id = f"F{next_n + i:03d}"
        out_ids.append(out_id)
        conn.execute(
            "INSERT OR IGNORE INTO factor_registry (factor_id, name, category, formula) VALUES (?,?,?,?)",
            (
                out_id,
                f"{name_prefix}_{fid}",
                "正交",
                json.dumps({"source": fid, "orthogonal_set": factor_ids}, ensure_ascii=False),
            ),
        )

    writes = 0
    for dt in dates:
        rows = conn.execute(
            f"""SELECT stock_id, factor_id, value FROM factor_values
                WHERE date=? AND factor_id IN ({ph}) AND value IS NOT NULL""",
            (dt, *factor_ids),
        ).fetchall()
        by_stock: Dict[int, Dict[str, float]] = {}
        for sid, fid, val in rows:
            by_stock.setdefault(sid, {})[fid] = float(val)
        stocks = [s for s, d in by_stock.items() if len(d) == len(factor_ids)]
        if len(stocks) < 8:
            continue
        matrix = [[by_stock[s][fid] for fid in factor_ids] for s in stocks]
        ortho = _gram_schmidt_columns(matrix)
        for ri, sid in enumerate(stocks):
            for ci, out_id in enumerate(out_ids):
                _upsert_factor(conn, sid, dt, out_id, ortho[ri][ci])
                writes += 1

    conn.commit()
    conn.close()
    return {
        "input_factors": factor_ids,
        "output_factors": out_ids,
        "dates_processed": len(dates),
        "cells_written": writes,
    }
