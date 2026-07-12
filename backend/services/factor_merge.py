"""因子合成 — equal / ic_ir 两种，默认 gated"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import date
from typing import List, Optional

from config import DB_PATH, FACTOR_MERGE_ENABLED
from services.factor_factory import init_factor_store, _upsert_factor
from services.ic_engine import analyze_factor_id

MIN_SAMPLE_DAYS = 60
MIN_STOCKS = 30


def _check_sample(factor_ids: List[str]) -> Optional[str]:
    conn = sqlite3.connect(DB_PATH)
    days = conn.execute(
        """SELECT COUNT(DISTINCT date) FROM factor_values WHERE factor_id=?""",
        (factor_ids[0],),
    ).fetchone()[0]
    stocks = conn.execute(
        """SELECT COUNT(DISTINCT stock_id) FROM factor_values WHERE factor_id=? AND date=(
            SELECT MAX(date) FROM factor_values WHERE factor_id=?)""",
        (factor_ids[0], factor_ids[0]),
    ).fetchone()[0]
    conn.close()
    if days < MIN_SAMPLE_DAYS or stocks < MIN_STOCKS:
        return f"insufficient_sample: days={days}, stocks={stocks} (need {MIN_SAMPLE_DAYS}/{MIN_STOCKS})"
    return None


def merge_factors_equal(factor_ids: List[str], name: str) -> dict:
    if not FACTOR_MERGE_ENABLED:
        return {"error": "AFR_FACTOR_MERGE_ENABLED=false"}
    err = _check_sample(factor_ids)
    if err:
        return {"error": err, "reason": err}

    conn = init_factor_store()
    latest = conn.execute("SELECT MAX(date) FROM factor_values").fetchone()[0]
    if not latest:
        conn.close()
        return {"error": "无因子数据"}

    stocks = conn.execute("SELECT id FROM stocks WHERE is_active=1").fetchall()
    merged_id = conn.execute(
        "SELECT factor_id FROM factor_registry WHERE name=? ORDER BY factor_id DESC LIMIT 1",
        (name,),
    ).fetchone()
    if merged_id:
        out_id = merged_id[0]
    else:
        max_row = conn.execute(
            "SELECT MAX(CAST(SUBSTR(factor_id,2) AS INTEGER)) FROM factor_registry WHERE factor_id LIKE 'F%'"
        ).fetchone()[0]
        n = (max_row or 15) + 1
        out_id = f"F{n:03d}"
        conn.execute(
            "INSERT INTO factor_registry (factor_id, name, category, formula) VALUES (?,?,?,?)",
            (out_id, name, "合成", f"equal({','.join(factor_ids)})"),
        )

    count = 0
    for (sid,) in stocks:
        vals = []
        for fid in factor_ids:
            row = conn.execute(
                "SELECT value FROM factor_values WHERE stock_id=? AND factor_id=? AND date=?",
                (sid, fid, latest),
            ).fetchone()
            if row and row[0] is not None:
                vals.append(float(row[0]))
        if vals:
            merged = sum(vals) / len(vals)
            _upsert_factor(conn, sid, latest, out_id, merged)
            count += 1

    meta = {
        "method": "equal",
        "inputs": factor_ids,
        "date": latest,
        "created": date.today().isoformat(),
    }
    conn.execute(
        "UPDATE factor_registry SET formula=? WHERE factor_id=?",
        (json.dumps(meta, ensure_ascii=False), out_id),
    )
    conn.commit()
    conn.close()
    return {"factor_id": out_id, "name": name, "method": "equal", "merged_count": count, "meta": meta}


def merge_factors_rolling_optimal(factor_ids: List[str], name: str, window: int = 30, forward_days: int = 20) -> dict:
    """滚动窗口 IC 最优权重合成（|IR| 加权，近 window 期）"""
    if not FACTOR_MERGE_ENABLED:
        return {"error": "AFR_FACTOR_MERGE_ENABLED=false"}
    err = _check_sample(factor_ids)
    if err:
        return {"error": err, "reason": err}

    weights = {}
    for fid in factor_ids:
        r = analyze_factor_id(fid, forward_days=forward_days, max_dates=window)
        series = r.get("ic_series") or []
        recent = [x["ic"] for x in series[-window:]]
        if len(recent) >= 5:
            mean_ic = sum(recent) / len(recent)
            std = math.sqrt(sum((x - mean_ic) ** 2 for x in recent) / max(len(recent) - 1, 1)) or 0.01
            ir = abs(mean_ic / std)
        else:
            ir = abs(r.get("ir") or 0)
        weights[fid] = ir if ir > 0 else 0.01
    total = sum(weights.values()) or 1.0
    weights = {k: v / total for k, v in weights.items()}

    conn = init_factor_store()
    latest = conn.execute("SELECT MAX(date) FROM factor_values").fetchone()[0]
    if not latest:
        conn.close()
        return {"error": "无因子数据"}

    max_row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(factor_id,2) AS INTEGER)) FROM factor_registry WHERE factor_id LIKE 'F%'"
    ).fetchone()[0]
    out_id = f"F{(max_row or 15) + 1:03d}"
    conn.execute(
        "INSERT INTO factor_registry (factor_id, name, category, formula) VALUES (?,?,?,?)",
        (out_id, name, "合成", f"rolling_opt({','.join(factor_ids)},w={window})"),
    )

    stocks = conn.execute("SELECT id FROM stocks WHERE is_active=1").fetchall()
    count = 0
    for (sid,) in stocks:
        merged = 0.0
        w_sum = 0.0
        for fid, w in weights.items():
            row = conn.execute(
                "SELECT value FROM factor_values WHERE stock_id=? AND factor_id=? AND date=?",
                (sid, fid, latest),
            ).fetchone()
            if row and row[0] is not None:
                merged += float(row[0]) * w
                w_sum += w
        if w_sum > 0:
            _upsert_factor(conn, sid, latest, out_id, merged / w_sum)
            count += 1

    meta = {"method": "rolling_optimal", "inputs": factor_ids, "weights": weights, "window": window, "date": latest}
    conn.execute(
        "UPDATE factor_registry SET formula=? WHERE factor_id=?",
        (json.dumps(meta, ensure_ascii=False), out_id),
    )
    conn.commit()
    conn.close()
    return {
        "factor_id": out_id,
        "name": name,
        "method": "rolling_optimal",
        "weights": weights,
        "merged_count": count,
        "meta": meta,
    }


def merge_factors_ic_ir(factor_ids: List[str], name: str, forward_days: int = 20) -> dict:
    if not FACTOR_MERGE_ENABLED:
        return {"error": "AFR_FACTOR_MERGE_ENABLED=false"}
    err = _check_sample(factor_ids)
    if err:
        return {"error": err, "reason": err}

    weights = {}
    for fid in factor_ids:
        r = analyze_factor_id(fid, forward_days=forward_days)
        ir = abs(r.get("ir") or 0)
        weights[fid] = ir if ir > 0 else 0.01
    total = sum(weights.values()) or 1.0
    weights = {k: v / total for k, v in weights.items()}

    conn = init_factor_store()
    latest = conn.execute("SELECT MAX(date) FROM factor_values").fetchone()[0]
    max_row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(factor_id,2) AS INTEGER)) FROM factor_registry WHERE factor_id LIKE 'F%'"
    ).fetchone()[0]
    out_id = f"F{(max_row or 15) + 1:03d}"
    conn.execute(
        "INSERT INTO factor_registry (factor_id, name, category, formula) VALUES (?,?,?,?)",
        (out_id, name, "合成", f"ic_ir({','.join(factor_ids)})"),
    )

    stocks = conn.execute("SELECT id FROM stocks WHERE is_active=1").fetchall()
    count = 0
    for (sid,) in stocks:
        merged = 0.0
        w_sum = 0.0
        for fid, w in weights.items():
            row = conn.execute(
                "SELECT value FROM factor_values WHERE stock_id=? AND factor_id=? AND date=?",
                (sid, fid, latest),
            ).fetchone()
            if row and row[0] is not None:
                merged += float(row[0]) * w
                w_sum += w
        if w_sum > 0:
            _upsert_factor(conn, sid, latest, out_id, merged / w_sum)
            count += 1

    meta = {"method": "ic_ir", "inputs": factor_ids, "weights": weights, "date": latest}
    conn.execute(
        "UPDATE factor_registry SET formula=? WHERE factor_id=?",
        (json.dumps(meta, ensure_ascii=False), out_id),
    )
    conn.commit()
    conn.close()
    return {"factor_id": out_id, "name": name, "method": "ic_ir", "weights": weights, "merged_count": count, "meta": meta}
