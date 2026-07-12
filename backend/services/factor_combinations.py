"""合成因子方案持久化 + 历史 materialize"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import date
from typing import Dict, List, Optional

from config import DB_PATH, FACTOR_MERGE_ENABLED
from services.factor_factory import _upsert_factor, init_factor_store
from services.ic_engine import analyze_factor_id

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS factor_combinations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    factor_ids_json TEXT NOT NULL,
    weight_method TEXT NOT NULL DEFAULT 'equal',
    weights_json TEXT,
    output_factor_id TEXT,
    formula_json TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_factor_combos_out ON factor_combinations(output_factor_id);
"""


def ensure_tables(conn: Optional[sqlite3.Connection] = None) -> None:
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    conn.executescript(CREATE_SQL)
    conn.commit()
    if own:
        conn.close()


def _allocate_factor_id(conn: sqlite3.Connection) -> str:
    max_row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(factor_id,2) AS INTEGER)) FROM factor_registry WHERE factor_id LIKE 'F%'"
    ).fetchone()[0]
    return f"F{(max_row or 15) + 1:03d}"


def _resolve_weights(factor_ids: List[str], method: str, weights: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    if weights and method == "custom":
        total = sum(weights.get(fid, 0) for fid in factor_ids) or 1.0
        return {fid: weights.get(fid, 0) / total for fid in factor_ids}

    if method == "ic_ir":
        raw = {}
        for fid in factor_ids:
            r = analyze_factor_id(fid, forward_days=20, max_dates=60)
            ir = abs(r.get("ir") or 0)
            raw[fid] = ir if ir > 0 else 0.01
        total = sum(raw.values()) or 1.0
        return {k: v / total for k, v in raw.items()}

    if method == "rolling_optimal":
        raw = {}
        for fid in factor_ids:
            r = analyze_factor_id(fid, forward_days=20, max_dates=30)
            series = r.get("ic_series") or []
            recent = [x["ic"] for x in series[-30:]]
            if len(recent) >= 5:
                mean_ic = sum(recent) / len(recent)
                std = math.sqrt(sum((x - mean_ic) ** 2 for x in recent) / max(len(recent) - 1, 1)) or 0.01
                raw[fid] = abs(mean_ic / std)
            else:
                raw[fid] = abs(r.get("ir") or 0) or 0.01
        total = sum(raw.values()) or 1.0
        return {k: v / total for k, v in raw.items()}

    n = len(factor_ids) or 1
    return {fid: 1.0 / n for fid in factor_ids}


def materialize_combination(
    combination_id: int,
    conn: Optional[sqlite3.Connection] = None,
) -> dict:
    """按方案权重回填 factor_values 历史 + 注册 output_factor_id。"""
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    ensure_tables(conn)
    init_factor_store()

    row = conn.execute(
        "SELECT * FROM factor_combinations WHERE id=?", (combination_id,)
    ).fetchone()
    if not row:
        if own:
            conn.close()
        return {"error": "combination_not_found"}

    cols = [d[1] for d in conn.execute("PRAGMA table_info(factor_combinations)").fetchall()]
    combo = dict(zip(cols, row))
    factor_ids: List[str] = json.loads(combo["factor_ids_json"])
    method = combo["weight_method"]
    weights = json.loads(combo["weights_json"]) if combo.get("weights_json") else None
    wmap = _resolve_weights(factor_ids, method, weights)

    out_id = combo.get("output_factor_id")
    if not out_id:
        out_id = _allocate_factor_id(conn)
        conn.execute(
            "INSERT INTO factor_registry (factor_id, name, category, formula) VALUES (?,?,?,?)",
            (out_id, combo["name"], "合成", f"combo#{combination_id}"),
        )
        conn.execute(
            "UPDATE factor_combinations SET output_factor_id=?, updated_at=datetime('now') WHERE id=?",
            (out_id, combination_id),
        )

    ph = ",".join("?" * len(factor_ids))
    dates = [
        r[0]
        for r in conn.execute(
            f"""SELECT date FROM factor_values WHERE factor_id IN ({ph})
                GROUP BY date HAVING COUNT(DISTINCT factor_id)=? ORDER BY date""",
            (*factor_ids, len(factor_ids)),
        ).fetchall()
    ]

    writes = 0
    for dt in dates:
        for sid_row in conn.execute("SELECT DISTINCT stock_id FROM factor_values WHERE date=?", (dt,)):
            sid = sid_row[0]
            vals = {}
            for fid in factor_ids:
                v = conn.execute(
                    "SELECT value FROM factor_values WHERE stock_id=? AND factor_id=? AND date=?",
                    (sid, fid, dt),
                ).fetchone()
                if v and v[0] is not None:
                    vals[fid] = float(v[0])
            if len(vals) < len(factor_ids):
                continue
            merged = sum(vals[fid] * wmap[fid] for fid in factor_ids)
            _upsert_factor(conn, sid, dt, out_id, merged)
            writes += 1

    formula = {
        "combination_id": combination_id,
        "method": method,
        "inputs": factor_ids,
        "weights": wmap,
        "materialized_dates": len(dates),
    }
    conn.execute(
        "UPDATE factor_registry SET formula=? WHERE factor_id=?",
        (json.dumps(formula, ensure_ascii=False), out_id),
    )
    conn.execute(
        "UPDATE factor_combinations SET weights_json=?, formula_json=?, updated_at=datetime('now') WHERE id=?",
        (json.dumps(wmap, ensure_ascii=False), json.dumps(formula, ensure_ascii=False), combination_id),
    )
    if own:
        conn.commit()
        conn.close()
    return {
        "combination_id": combination_id,
        "output_factor_id": out_id,
        "materialized_cells": writes,
        "dates": len(dates),
        "weights": wmap,
    }


def create_combination(
    name: str,
    factor_ids: List[str],
    weight_method: str = "equal",
    weights: Optional[Dict[str, float]] = None,
    materialize: bool = True,
) -> dict:
    if not FACTOR_MERGE_ENABLED:
        return {"error": "AFR_FACTOR_MERGE_ENABLED=false"}
    if len(factor_ids) < 2:
        return {"error": "至少选择 2 个因子"}
    if weight_method not in ("equal", "ic_ir", "rolling_optimal", "custom"):
        return {"error": f"未知 weight_method: {weight_method}"}

    ensure_tables()
    conn = sqlite3.connect(DB_PATH)
    wmap = _resolve_weights(factor_ids, weight_method, weights)
    conn.execute(
        """INSERT INTO factor_combinations
           (name, factor_ids_json, weight_method, weights_json, formula_json)
           VALUES (?,?,?,?,?)""",
        (
            name,
            json.dumps(factor_ids, ensure_ascii=False),
            weight_method,
            json.dumps(wmap, ensure_ascii=False),
            json.dumps({"inputs": factor_ids, "method": weight_method}, ensure_ascii=False),
        ),
    )
    combo_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()

    out = {
        "id": combo_id,
        "name": name,
        "factor_ids": factor_ids,
        "weight_method": weight_method,
        "weights": wmap,
    }
    if materialize:
        out["materialize"] = materialize_combination(combo_id)
        out["output_factor_id"] = out["materialize"].get("output_factor_id")
    return out


def list_combinations(limit: int = 50) -> List[dict]:
    ensure_tables()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM factor_combinations ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["factor_ids"] = json.loads(d.pop("factor_ids_json"))
        d["weights"] = json.loads(d["weights_json"]) if d.get("weights_json") else {}
        out.append(d)
    return out


def get_combination(combination_id: int) -> Optional[dict]:
    ensure_tables()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM factor_combinations WHERE id=?", (combination_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["factor_ids"] = json.loads(d.pop("factor_ids_json"))
    d["weights"] = json.loads(d["weights_json"]) if d.get("weights_json") else {}
    if d.get("formula_json"):
        try:
            d["formula"] = json.loads(d["formula_json"])
        except json.JSONDecodeError:
            d["formula"] = {}
    return d


def delete_combination(combination_id: int) -> dict:
    ensure_tables()
    conn = sqlite3.connect(DB_PATH)
    combo = get_combination(combination_id)
    if not combo:
        conn.close()
        return {"error": "not_found"}
    conn.execute("DELETE FROM factor_combinations WHERE id=?", (combination_id,))
    conn.commit()
    conn.close()
    return {"deleted": combination_id, "output_factor_id": combo.get("output_factor_id")}


def load_factor_score_snap(
    factor_id: str,
    start_str: str,
    end_str: str,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Dict[str, float]]:
    """code -> calc_date -> score，供回测使用。"""
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT s.code, fv.date, fv.value
           FROM factor_values fv
           JOIN stocks s ON fv.stock_id = s.id
           WHERE fv.factor_id=? AND fv.date BETWEEN ? AND ?
           ORDER BY fv.date""",
        (factor_id, start_str, end_str),
    ).fetchall()
    if own:
        conn.close()
    snap: Dict[str, Dict[str, float]] = {}
    for r in rows:
        snap.setdefault(r["code"], {})[r["date"]] = float(r["value"])
    return snap
