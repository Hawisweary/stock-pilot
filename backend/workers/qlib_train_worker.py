"""Qlib / LightGBM 训练 worker — 多 horizon 独立模型 + 分周期特征选配。"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import defaultdict

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from services.ml_feature_sets import (  # noqa: E402
    MlFeatureContext,
    apply_cross_section_ranks,
    compute_base_features,
    feature_names_for,
    vectorize,
)

DEFAULT_HORIZONS = (5, 20, 60)
MIN_TRAIN_SAMPLES = 120
LOOKBACK_BY_HORIZON = {5: 20, 20: 30, 60: 260}


def model_version_for(mode: str, forward_days: int) -> str:
    return f"{mode}_h{forward_days}"


def _demo_train(db_path: str, forward_days: int = 20) -> dict:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS ml_predictions (
            stock_id INTEGER NOT NULL, pred_date TEXT NOT NULL,
            score REAL NOT NULL, model_version TEXT NOT NULL DEFAULT 'v0',
            PRIMARY KEY (stock_id, pred_date, model_version)
        )"""
    )
    latest = conn.execute("SELECT MAX(calc_date) FROM comprehensive_scores").fetchone()[0]
    if not latest:
        conn.close()
        return {"status": "error", "reason": "no comprehensive_scores", "forward_days": forward_days}
    rows = conn.execute(
        "SELECT stock_id, composite_score FROM comprehensive_scores WHERE calc_date=?",
        (latest,),
    ).fetchall()
    mv = model_version_for("demo", forward_days)
    n = 0
    for sid, score in rows:
        if score is not None:
            conn.execute(
                "INSERT OR REPLACE INTO ml_predictions VALUES (?,?,?,?)",
                (sid, latest, float(score), mv),
            )
            n += 1
    conn.commit()
    conn.close()
    return {
        "status": "done",
        "mode": "demo",
        "forward_days": forward_days,
        "model_version": mv,
        "predictions_written": n,
        "pred_date": latest,
    }


def _load_quote_panel(db_path: str) -> tuple[dict[str, list], dict[str, int], list[str]]:
    conn = sqlite3.connect(db_path)
    dates = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT trade_date FROM stock_daily_quotes ORDER BY trade_date"
        ).fetchall()
    ]
    by_code: dict[str, list] = defaultdict(list)
    code_to_id: dict[str, int] = {}
    for r in conn.execute(
        """SELECT s.id, s.code, q.trade_date,
                  COALESCE(q.adj_close, q.close), q.volume,
                  COALESCE(q.high, q.close), COALESCE(q.low, q.close),
                  COALESCE(q.turnover, 0), COALESCE(q.amount, 0)
           FROM stock_daily_quotes q JOIN stocks s ON q.stock_id=s.id
           WHERE s.is_active=1 AND COALESCE(q.adj_close, q.close) IS NOT NULL
           ORDER BY s.code, q.trade_date"""
    ).fetchall():
        code_to_id[r[1]] = int(r[0])
        by_code[r[1]].append(
            (r[2], float(r[3]), float(r[4] or 0), float(r[5]), float(r[6]), float(r[7]), float(r[8]))
        )
    conn.close()
    return by_code, code_to_id, dates


def _min_bars(horizon: int, forward_days: int) -> int:
    return LOOKBACK_BY_HORIZON.get(horizon, 30) + forward_days + 5


def _build_training_panel(
    by_code: dict[str, list],
    code_to_id: dict[str, int],
    dates: list[str],
    ctx: MlFeatureContext,
    *,
    train_days: int,
    forward_days: int,
) -> tuple[list[list[float]], list[float], str]:
    lookback = LOOKBACK_BY_HORIZON.get(forward_days, 30)
    if len(dates) < lookback + forward_days + 10:
        return [], [], ""

    window_dates = set(dates[-(train_days + forward_days + lookback) :])
    pred_date = dates[-1]
    min_bars = _min_bars(forward_days, forward_days)
    pending: dict[str, list[tuple[str, int, int, float]]] = defaultdict(list)

    for code, series in by_code.items():
        sid = code_to_id.get(code)
        if not sid or len(series) < min_bars:
            continue
        for i in range(lookback, len(series) - forward_days):
            dt = series[i][0]
            if dt not in window_dates:
                continue
            close = series[i][1]
            fwd = series[i + forward_days][1]
            label = (fwd / close - 1) if close > 0 else 0.0
            pending[dt].append((code, sid, i, label))

    X, y = [], []
    for dt in sorted(pending.keys()):
        batch: list[dict] = []
        meta: list[tuple[int, float]] = []
        for code, sid, i, label in pending[dt]:
            feats = compute_base_features(by_code[code], i, forward_days, sid, ctx)
            batch.append(feats)
            meta.append((sid, label))
        if len(batch) < 2:
            continue
        apply_cross_section_ranks(batch, forward_days)
        for feats, (_sid, label) in zip(batch, meta):
            X.append(vectorize(feats, forward_days))
            y.append(label)

    return X, y, pred_date


def _predict_latest(
    by_code: dict[str, list],
    code_to_id: dict[str, int],
    stocks: list[tuple[int, str]],
    ctx: MlFeatureContext,
    model,
    *,
    forward_days: int,
    pred_date: str,
) -> list[tuple[int, float]]:
    lookback = LOOKBACK_BY_HORIZON.get(forward_days, 30)
    batch: list[dict] = []
    sids: list[int] = []
    import numpy as np

    for sid, code in stocks:
        series = by_code.get(code, [])
        if len(series) < lookback + 5:
            continue
        i = len(series) - 1
        if series[i][0] != pred_date:
            i = next((j for j in range(len(series) - 1, -1, -1) if series[j][0] == pred_date), -1)
            if i < lookback:
                continue
        feats = compute_base_features(series, i, forward_days, sid, ctx)
        batch.append(feats)
        sids.append(sid)

    if len(batch) < 1:
        return []
    apply_cross_section_ranks(batch, forward_days)
    X = np.array([vectorize(f, forward_days) for f in batch], dtype=np.float32)
    preds = model.predict(X)
    out = []
    for sid, pred in zip(sids, preds):
        score = round(max(0, min(100, 50 + float(pred) * 500)), 2)
        out.append((sid, score))
    return out


def _lightgbm_train(
    db_path: str,
    *,
    train_days: int = 120,
    forward_days: int = 20,
) -> dict:
    by_code, code_to_id, dates = _load_quote_panel(db_path)
    conn = sqlite3.connect(db_path)
    ctx = MlFeatureContext.load(conn, dates)
    conn.close()

    X, y, pred_date = _build_training_panel(
        by_code, code_to_id, dates, ctx, train_days=train_days, forward_days=forward_days
    )
    feat_names = feature_names_for(forward_days)
    if len(X) < MIN_TRAIN_SAMPLES:
        r = _demo_train(db_path, forward_days)
        r["note"] = f"insufficient panel n={len(X)} h={forward_days}; demo fallback"
        return r

    import numpy as np

    X_arr = np.array(X, dtype=np.float32)
    y_arr = np.array(y, dtype=np.float32)
    split = int(len(X_arr) * 0.85)

    model = None
    mode = "lightgbm"
    try:
        import lightgbm as lgb

        train_data = lgb.Dataset(X_arr[:split], label=y_arr[:split], feature_name=feat_names)
        model = lgb.train(
            {"objective": "regression", "metric": "l2", "verbosity": -1, "num_leaves": 31},
            train_data,
            num_boost_round=80,
        )
    except Exception:
        from sklearn.linear_model import Ridge

        model = Ridge(alpha=1.0).fit(X_arr[:split], y_arr[:split])
        mode = "ridge"

    mv = model_version_for(mode, forward_days)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS ml_predictions (
            stock_id INTEGER NOT NULL, pred_date TEXT NOT NULL,
            score REAL NOT NULL, model_version TEXT NOT NULL DEFAULT 'v0',
            PRIMARY KEY (stock_id, pred_date, model_version)
        )"""
    )
    stocks = conn.execute("SELECT id, code FROM stocks WHERE is_active=1").fetchall()
    preds = _predict_latest(
        by_code, code_to_id, stocks, ctx, model, forward_days=forward_days, pred_date=pred_date
    )
    n = 0
    for sid, score in preds:
        conn.execute(
            "INSERT OR REPLACE INTO ml_predictions VALUES (?,?,?,?)",
            (sid, pred_date, score, mv),
        )
        n += 1
    conn.commit()
    conn.close()
    return {
        "status": "done",
        "mode": mode,
        "forward_days": forward_days,
        "model_version": mv,
        "feature_count": len(feat_names),
        "features": feat_names,
        "predictions_written": n,
        "pred_date": pred_date,
        "train_samples": len(X),
    }


def _parse_horizons(payload: dict) -> list[int]:
    if "horizons" in payload and payload["horizons"]:
        raw = payload["horizons"]
        if isinstance(raw, (list, tuple)):
            return sorted({int(h) for h in raw if int(h) > 0})
        return [int(raw)]
    if "forward_days" in payload:
        return [int(payload["forward_days"])]
    return list(DEFAULT_HORIZONS)


def main() -> int:
    payload = json.loads(sys.argv[1] if len(sys.argv) > 1 else "{}")
    db_path = os.environ.get("AFR_DB_PATH", "data/afr.db")
    train_days = int(payload.get("train_days", 120))
    horizons = _parse_horizons(payload)

    try:
        import qlib  # noqa: F401

        qlib_note = "qlib present; LightGBM pipeline"
    except ImportError:
        qlib_note = "qlib absent; LightGBM/sklearn pipeline"

    results = []
    for h in horizons:
        r = _lightgbm_train(db_path, train_days=train_days, forward_days=h)
        r["note"] = qlib_note
        results.append(r)

    ok = [r for r in results if r.get("status") == "done"]
    out = {
        "status": "done" if ok else "error",
        "train_days": train_days,
        "horizons": horizons,
        "models": results,
        "predictions_written": sum(int(r.get("predictions_written") or 0) for r in ok),
    }
    if not ok:
        out["reason"] = "all horizons failed"
    print(json.dumps(out, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
