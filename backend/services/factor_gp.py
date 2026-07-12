"""遗传规划因子挖掘 — 轻量随机搜索 + IC 适应度"""
from __future__ import annotations

import json
import random
import sqlite3
from typing import List, Optional

from config import DB_PATH

_TEMPLATES = [
    "Delta($adj_close, {n})",
    "Mean($adj_close, {n})",
    "Std($adj_close, {n}) / Mean($adj_close, {n})",
    "Delta($volume, {n}) / Mean($volume, {n})",
    "Mean($adj_close, {n}) - Mean($adj_close, {m})",
    "Rank(Delta($adj_close, {n}))",
    "($high - $low) / $adj_close",
    "Mean($adj_close, 5) / Mean($adj_close, 20) - 1",
]

_GP_NS = [1, 3, 5, 10, 20]


def _random_formula() -> str:
    tpl = random.choice(_TEMPLATES)
    return tpl.format(n=random.choice(_GP_NS), m=random.choice(_GP_NS))


def _fitness_ic(factor_id: str, forward_days: int = 20) -> float:
    from services.ic_engine import analyze_factor_id

    r = analyze_factor_id(factor_id, forward_days=forward_days)
    ic = r.get("mean_ic")
    if ic is None:
        return -999.0
    try:
        return float(ic)
    except (TypeError, ValueError):
        return -999.0


def run_gp_search(
    *,
    population: int = 12,
    generations: int = 8,
    forward_days: int = 20,
    top_k: int = 3,
) -> dict:
    """运行 GP 搜索，注册 top_k 候选因子。"""
    from services.factor_expression import compute_expression, validate_expression

    candidates: List[dict] = []
    seen: set[str] = set()

    for _ in range(population * generations):
        formula = _random_formula()
        if formula in seen:
            continue
        seen.add(formula)
        v = validate_expression(formula)
        if not v.get("valid"):
            continue
        name = f"gp_{len(candidates)+1}"
        out = compute_expression(formula, name)
        if out.get("error"):
            continue
        fid = out["factor_id"]
        ic = _fitness_ic(fid, forward_days=forward_days)
        candidates.append(
            {
                "factor_id": fid,
                "formula": formula,
                "mean_ic": ic,
                "computed": out.get("computed", 0),
            }
        )

    candidates.sort(key=lambda x: x["mean_ic"], reverse=True)
    winners = candidates[:top_k]
    run_id = _log_gp_run(winners, population, generations)
    return {
        "run_id": run_id,
        "evaluated": len(candidates),
        "winners": winners,
        "forward_days": forward_days,
    }


def _log_gp_run(winners: List[dict], population: int, generations: int) -> int:
    ensure_gp_tables()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        """INSERT INTO factor_gp_runs (status, population, generations, candidates_json)
           VALUES ('done', ?, ?, ?)""",
        (population, generations, json.dumps(winners, ensure_ascii=False)),
    )
    conn.commit()
    rid = int(cur.lastrowid)
    conn.close()
    return rid


def ensure_gp_tables(conn: Optional[sqlite3.Connection] = None) -> None:
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS factor_gp_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT,
            status TEXT DEFAULT 'pending',
            population INTEGER,
            generations INTEGER,
            candidates_json TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    if own:
        conn.close()


def list_gp_runs(limit: int = 20) -> List[dict]:
    ensure_gp_tables()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM factor_gp_runs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("candidates_json"):
            try:
                d["candidates"] = json.loads(d["candidates_json"])
            except json.JSONDecodeError:
                d["candidates"] = []
        out.append(d)
    return out
