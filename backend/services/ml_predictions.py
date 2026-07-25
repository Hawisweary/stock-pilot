"""ML 预测表与 Qlib 训练 job（多 horizon 方案 A）"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import date
from typing import Optional

from config import (
    DB_PATH,
    ML_DEFAULT_HORIZON,
    ML_HORIZONS,
    ML_GATE_MIN_FOLDS,
    ML_GATE_MIN_MEAN_RANK_IC,
    ML_GATE_RECENT_FOLDS,
    QLIB_ENABLED,
    QUANT_WORKERS_DIR,
    VENV_QUANT_PYTHON,
)


def model_version_for_horizon(forward_days: int, mode: str = "lightgbm") -> str:
    return f"{mode}_h{forward_days}"


def is_demo_model_version(model_version: str | None) -> bool:
    """demo / seed 写入的预测，非 LightGBM/Ridge 训练产物。"""
    return bool(model_version and str(model_version).startswith("demo_"))


def resolve_model_version_for_horizon(
    conn: sqlite3.Connection,
    horizon: int,
) -> str | None:
    """优先 WF live 模型，其次 legacy lightgbm/ridge，最后 demo。"""
    patterns = [
        f"%_h{horizon}_wf_live",
        f"lightgbm_h{horizon}_wf_v2",
        f"ridge_h{horizon}_wf_v2",
        f"lightgbm_h{horizon}_wf_v1",
        f"ridge_h{horizon}_wf_v1",
        f"lightgbm_h{horizon}",
        f"ridge_h{horizon}",
        f"demo_h{horizon}",
    ]
    for pat in patterns:
        row = conn.execute(
            """SELECT model_version FROM ml_predictions
               WHERE model_version LIKE ? ORDER BY pred_date DESC LIMIT 1""",
            (pat,),
        ).fetchone()
        if row:
            return str(row[0])
    return None


def ensure_ml_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS ml_predictions (
            stock_id INTEGER NOT NULL,
            pred_date TEXT NOT NULL,
            score REAL NOT NULL,
            model_version TEXT NOT NULL DEFAULT 'v0',
            PRIMARY KEY (stock_id, pred_date, model_version)
        )"""
    )
    conn.commit()


def get_latest_predictions(
    pred_date: Optional[str] = None,
    limit: int = 50,
    *,
    horizon: Optional[int] = None,
) -> list[dict]:
    h = horizon if horizon is not None else ML_DEFAULT_HORIZON
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_ml_tables(conn)
    mv = resolve_model_version_for_horizon(conn, h) or model_version_for_horizon(h)
    dt = pred_date or conn.execute(
        "SELECT MAX(pred_date) FROM ml_predictions WHERE model_version=?",
        (mv,),
    ).fetchone()[0]
    if not dt:
        conn.close()
        return []
    rows = conn.execute(
        """SELECT s.code, s.name, mp.score, mp.pred_date, mp.model_version,
                  cs.composite_v5
           FROM ml_predictions mp
           JOIN stocks s ON mp.stock_id=s.id
           LEFT JOIN (
             SELECT stock_id, composite_v5 FROM comprehensive_scores
             WHERE calc_date = (SELECT MAX(calc_date) FROM comprehensive_scores WHERE composite_v5 IS NOT NULL)
           ) cs ON cs.stock_id = mp.stock_id
           WHERE mp.pred_date=? AND mp.model_version=?
           ORDER BY mp.score DESC LIMIT ?""",
        (dt, mv, limit),
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        row = dict(r)
        mv = row.get("model_version")
        row["is_demo"] = is_demo_model_version(mv)
        out.append(row)
    return out


def list_ml_horizons(pred_date: Optional[str] = None) -> list[dict]:
    """各 horizon 最新预测日及样本数。"""
    conn = sqlite3.connect(DB_PATH)
    ensure_ml_tables(conn)
    out = []
    for h in ML_HORIZONS:
        mv = model_version_for_horizon(h)
        row = conn.execute(
            """SELECT pred_date, COUNT(*) FROM ml_predictions
               WHERE model_version=? GROUP BY pred_date ORDER BY pred_date DESC LIMIT 1""",
            (mv,),
        ).fetchone()
        if row:
            out.append(
                {
                    "horizon": h,
                    "model_version": mv,
                    "pred_date": row[0],
                    "count": row[1],
                }
            )
    conn.close()
    return out


def load_prediction_scores(
    pred_date: str,
    *,
    horizon: Optional[int] = None,
) -> dict[str, float]:
    """code -> score，供 ml_pred 回测"""
    from services.ml_gate import is_ml_predictions_approved

    if not is_ml_predictions_approved(horizon=horizon):
        return {}
    h = horizon if horizon is not None else ML_DEFAULT_HORIZON
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_ml_tables(conn)
    mv = resolve_model_version_for_horizon(conn, h) or model_version_for_horizon(h)
    rows = conn.execute(
        """SELECT s.code, mp.score FROM ml_predictions mp
           JOIN stocks s ON mp.stock_id=s.id
           WHERE mp.pred_date=? AND mp.model_version=?""",
        (pred_date, mv),
    ).fetchall()
    conn.close()
    return {r["code"]: float(r["score"]) for r in rows}


def run_qlib_train_job(payload: dict | None = None) -> dict:
    """训练一个或多个 horizon 模型（子进程）。"""
    if not QLIB_ENABLED:
        return {"status": "skipped", "reason": "AFR_QLIB_ENABLED=false"}

    payload = dict(payload or {})
    if "horizons" not in payload and "forward_days" not in payload:
        payload["horizons"] = list(ML_HORIZONS)

    worker = os.path.join(QUANT_WORKERS_DIR, "qlib_train_worker.py")
    if not os.path.isfile(worker):
        return {"status": "error", "reason": f"worker missing: {worker}"}

    python = VENV_QUANT_PYTHON or sys.executable
    env = os.environ.copy()
    env["AFR_DB_PATH"] = DB_PATH
    proc = subprocess.run(
        [python, worker, json.dumps(payload)],
        capture_output=True,
        text=True,
        timeout=int(payload.get("timeout_sec", 900)),
        env=env,
    )
    if proc.returncode != 0:
        return {
            "status": "error",
            "stderr": proc.stderr[-1000:],
            "stdout": proc.stdout[-500:],
        }
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except json.JSONDecodeError:
        return {"status": "done", "raw": proc.stdout[-500:]}


def seed_demo_predictions() -> int:
    """测试/演示：用 composite_score 写入 ml_predictions（非真实 ML）"""
    conn = sqlite3.connect(DB_PATH)
    ensure_ml_tables(conn)
    today = date.today().strftime("%Y-%m-%d")
    rows = conn.execute(
        """SELECT cs.stock_id, cs.composite_score FROM comprehensive_scores cs
           WHERE cs.calc_date=(SELECT MAX(calc_date) FROM comprehensive_scores)"""
    ).fetchall()
    n = 0
    for sid, score in rows:
        if score is None:
            continue
        for h in ML_HORIZONS:
            conn.execute(
                "INSERT OR REPLACE INTO ml_predictions (stock_id, pred_date, score, model_version) VALUES (?,?,?,?)",
                (sid, today, float(score), model_version_for_horizon(h, "demo")),
            )
            n += 1
    conn.commit()
    conn.close()
    return n


def sync_ml_to_comprehensive(
    ml_weight: float = 0.08,
    db_path: str | None = None,
    *,
    horizon: Optional[int] = None,
) -> dict:
    """将指定 horizon 的 ML 预测分混入 composite_score"""
    from services.ml_gate import is_ml_predictions_approved

    path = db_path or DB_PATH
    if not is_ml_predictions_approved(path, horizon=horizon):
        return {"error": "ml_metric_gate_not_passed", "updated": 0}

    h = horizon if horizon is not None else ML_DEFAULT_HORIZON
    mv = model_version_for_horizon(h)

    conn = sqlite3.connect(path)
    ensure_ml_tables(conn)
    pred_date = conn.execute(
        "SELECT MAX(pred_date) FROM ml_predictions WHERE model_version=?",
        (mv,),
    ).fetchone()[0]
    if not pred_date:
        conn.close()
        return {"error": f"no predictions for {mv}", "updated": 0}

    rows = conn.execute(
        """SELECT mp.stock_id, mp.score, cs.composite_score, cs.calc_date
           FROM ml_predictions mp
           LEFT JOIN comprehensive_scores cs ON cs.stock_id=mp.stock_id AND cs.calc_date=mp.pred_date
           WHERE mp.pred_date=? AND mp.model_version=?""",
        (pred_date, mv),
    ).fetchall()

    updated = 0
    w = max(0.0, min(0.3, ml_weight))
    for sid, ml_score, comp, calc_dt in rows:
        if ml_score is None:
            continue
        base = float(comp) if comp is not None else 50.0
        blended = round((1 - w) * base + w * float(ml_score), 2)
        dt = calc_dt or pred_date
        existing = conn.execute(
            "SELECT id FROM comprehensive_scores WHERE stock_id=? AND calc_date=?",
            (sid, dt),
        ).fetchone()
        breakdown = {
            "ml_blend": True,
            "ml_weight": w,
            "ml_score": ml_score,
            "ml_horizon": h,
            "model_version": mv,
            "base": base,
        }
        # v3.0: composite_score 已废弃，仅保留 breakdown_json 供分析。
        # ML 混合分不再写入 composite_score；调用方可触发 V5 重算以更新 composite_v5。
        if existing:
            conn.execute(
                "UPDATE comprehensive_scores SET breakdown_json=? WHERE stock_id=? AND calc_date=?",
                (json.dumps(breakdown, ensure_ascii=False), sid, dt),
            )
        updated += 1
    conn.commit()
    conn.close()
    return {
        "updated": updated,
        "pred_date": pred_date,
        "ml_weight": w,
        "horizon": h,
        "model_version": mv,
    }


def get_ml_train_status(horizon: int | None = None) -> dict:
    from config import ML_DEFAULT_HORIZON
    from services.ml_gate import ml_predictions_gate_status
    from services.ml_train_store import get_validation_summary

    h = horizon if horizon is not None else ML_DEFAULT_HORIZON
    summary = get_validation_summary(
        DB_PATH,
        horizon=h,
        recent_windows=ML_GATE_RECENT_FOLDS,
        rank_ic_threshold=ML_GATE_MIN_MEAN_RANK_IC,
        min_folds=ML_GATE_MIN_FOLDS,
    )
    gate = ml_predictions_gate_status(DB_PATH, horizon=h)
    summary["gate"] = gate
    summary["predictions_approved"] = gate["approved"]
    return summary
