"""自定义因子 — 公式解析、计算、相关矩阵"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from typing import Dict, List, Optional

from config import DB_PATH

# F001-F008 映射到 comprehensive_scores 列
FACTOR_REF = {
    "F001": "composite_score",
    "F002": "fundamental_score",
    "F003": "technical_score",
    "F004": "sentiment_score",
    "F005": "capital_score",
    "F006": "policy_score",
    "F007": "mood_score",
    "F008": "val_score",
}

ALLOWED_TOKENS = re.compile(r"^[\d\.\+\-\*/\(\)\sF]+$")


def init_custom_factor_tables(conn: Optional[sqlite3.Connection] = None) -> None:
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS custom_factors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            factor_id TEXT UNIQUE,
            name TEXT NOT NULL,
            formula TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    if own:
        conn.close()


def list_custom_factors() -> List[dict]:
    init_custom_factor_tables()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM custom_factors ORDER BY id").fetchall()]
    conn.close()
    return rows


def create_custom_factor(name: str, formula: str) -> dict:
    init_custom_factor_tables()
    formula = formula.strip().upper()
    if not ALLOWED_TOKENS.match(formula.replace(" ", "")):
        return {"error": "公式仅允许 F001-F015、数字与 +-*/()"}
    # 试算
    test = _eval_formula(formula, {f"F{i:03d}": 50.0 for i in range(1, 16)})
    if test is None:
        return {"error": "公式无法解析"}
    fid = f"C{date.today().strftime('%m%d')}_{len(list_custom_factors()) + 1:02d}"
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "INSERT INTO custom_factors (factor_id, name, formula) VALUES (?,?,?)",
            (fid, name, formula),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return {"error": "因子 ID 冲突"}
    conn.close()
    compute_custom_factor(fid)
    return {"factor_id": fid, "name": name, "formula": formula}


def _eval_formula(formula: str, env: Dict[str, float]) -> Optional[float]:
    expr = formula.upper()
    for k, v in sorted(env.items(), key=lambda x: -len(x[0])):
        expr = expr.replace(k, str(v))
    if not re.match(r"^[\d\.\+\-\*/\(\)\s]+$", expr.replace(" ", "")):
        return None
    try:
        return round(float(eval(expr, {"__builtins__": {}}, {})), 4)  # noqa: S307
    except Exception:
        return None


def _load_factor_env(conn: sqlite3.Connection, stock_id: int, dt: str) -> Dict[str, float]:
    env: Dict[str, float] = {}
    row = conn.execute(
        """SELECT composite_score, fundamental_score, technical_score, sentiment_score,
                  capital_score, policy_score, mood_score, val_score
           FROM comprehensive_scores WHERE stock_id=? AND calc_date=?""",
        (stock_id, dt),
    ).fetchone()
    if row:
        keys = ["F001", "F002", "F003", "F004", "F005", "F006", "F007", "F008"]
        for i, k in enumerate(keys):
            if row[i] is not None:
                env[k] = float(row[i])
    fv = conn.execute(
        "SELECT factor_id, value FROM factor_values WHERE stock_id=? AND date=?",
        (stock_id, dt),
    ).fetchall()
    for fid, val in fv:
        if val is not None:
            env[fid] = float(val)
    return env


def compute_custom_factor(factor_id: str) -> dict:
    from services.factor_factory import init_factor_store, _upsert_factor

    init_custom_factor_tables()
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT formula FROM custom_factors WHERE factor_id=?", (factor_id,)
    ).fetchone()
    if not row:
        conn.close()
        return {"error": "自定义因子不存在"}
    formula = row[0]
    calc_date = conn.execute("SELECT MAX(calc_date) FROM comprehensive_scores").fetchone()[0]
    if not calc_date:
        conn.close()
        return {"error": "无评分数据"}
    init_factor_store()
    stocks = conn.execute("SELECT id FROM stocks WHERE is_active=1").fetchall()
    count = 0
    for (sid,) in stocks:
        env = _load_factor_env(conn, sid, calc_date)
        val = _eval_formula(formula, env)
        if val is not None:
            _upsert_factor(conn, sid, calc_date, factor_id, val)
            count += 1
    conn.execute(
        "INSERT OR IGNORE INTO factor_registry (factor_id, name, category, formula) VALUES (?,?,?,?)",
        (factor_id, conn.execute("SELECT name FROM custom_factors WHERE factor_id=?", (factor_id,)).fetchone()[0], "自定义", formula),
    )
    conn.commit()
    conn.close()
    return {"factor_id": factor_id, "computed": count, "date": calc_date}


def factor_correlation_matrix(factor_ids: Optional[List[str]] = None) -> dict:
    init_custom_factor_tables()
    conn = sqlite3.connect(DB_PATH)
    latest = conn.execute("SELECT MAX(date) FROM factor_values").fetchone()[0]
    if not latest:
        conn.close()
        return {"error": "无因子数据"}
    if not factor_ids:
        factor_ids = [r[0] for r in conn.execute(
            "SELECT DISTINCT factor_id FROM factor_values WHERE date=? ORDER BY factor_id LIMIT 15",
            (latest,),
        ).fetchall()]
    matrix = {}
    vectors: Dict[str, List[float]] = {}
    for fid in factor_ids:
        rows = conn.execute(
            """SELECT fv.value FROM factor_values fv
               JOIN stocks s ON fv.stock_id=s.id
               WHERE fv.factor_id=? AND fv.date=? AND s.is_active=1 AND fv.value IS NOT NULL
               ORDER BY fv.stock_id""",
            (fid, latest),
        ).fetchall()
        vectors[fid] = [r[0] for r in rows]
    for a in factor_ids:
        matrix[a] = {}
        for b in factor_ids:
            va, vb = vectors.get(a, []), vectors.get(b, [])
            n = min(len(va), len(vb))
            if n < 5:
                matrix[a][b] = None
                continue
            xs, ys = va[:n], vb[:n]
            mx, my = sum(xs) / n, sum(ys) / n
            sx = (sum((x - mx) ** 2 for x in xs) / n) ** 0.5
            sy = (sum((y - my) ** 2 for y in ys) / n) ** 0.5
            if sx == 0 or sy == 0:
                matrix[a][b] = 0
            else:
                cov = sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / n
                matrix[a][b] = round(cov / (sx * sy), 3)
    conn.close()
    return {"date": latest, "factors": factor_ids, "matrix": matrix}
