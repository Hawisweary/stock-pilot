"""H20 Purged Walk-Forward 训练与 OOS 落库。"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from services.ml_quotes import load_quote_panel

from services.ml_cv import (
    iter_walkforward_windows,
    purge_train_mask,
    split_valid_purged,
)
from services.ml_feature_sets import (
    MlFeatureContext,
    apply_cross_section_ranks,
    compute_base_features,
    feature_names_for,
    vectorize,
)
from services.ml_metrics import long_short_return, pearson_ic, rmse, spearman_rank_ic
from services.ml_train_store import (
    configure_sqlite_conn,
    ensure_ml_validation_tables,
    insert_train_run,
    upsert_oos_daily,
)

LOOKBACK_BY_HORIZON = {5: 20, 20: 30, 60: 260}
WF_TRAIN_WINDOW_DAYS = 480
WF_STEP_DAYS = 20
WF_EMBARGO_DAYS = 5
WF_MIN_HISTORY_DAYS = WF_TRAIN_WINDOW_DAYS + WF_STEP_DAYS + 40
MIN_TRAIN_SAMPLES = 120
MIN_FOLDS = 2


@dataclass
class LabeledSample:
    stock_id: int
    feature_date: str
    feature_idx: int
    x: list[float]
    y: float


def _date_to_idx(dates: list[str]) -> dict[str, int]:
    return {d: i for i, d in enumerate(dates)}


def _min_bars(forward_days: int) -> int:
    return LOOKBACK_BY_HORIZON.get(forward_days, 30) + forward_days + 5


def _build_cross_section(
    by_code: dict[str, list],
    code_to_id: dict[str, int],
    dates: list[str],
    ctx: MlFeatureContext,
    pred_date: str,
    forward_days: int,
    *,
    with_labels: bool = False,
    variant: str = "v2",
) -> list[LabeledSample]:
    lookback = LOOKBACK_BY_HORIZON.get(forward_days, 30)
    d_idx = _date_to_idx(dates)
    fi = d_idx.get(pred_date, -1)
    if fi < 0:
        return []
    out: list[LabeledSample] = []
    pending: list[tuple[int, dict, float | None]] = []

    for code, series in by_code.items():
        sid = code_to_id.get(code)
        if not sid:
            continue
        i = next((j for j, bar in enumerate(series) if bar[0] == pred_date), -1)
        if i < lookback or i + forward_days >= len(series):
            continue
        feats = compute_base_features(series, i, forward_days, sid, ctx, variant)
        label = None
        if with_labels:
            close = series[i][1]
            fwd = series[i + forward_days][1]
            label = (fwd / close - 1) if close > 0 else 0.0
        pending.append((sid, feats, label))

    if len(pending) < 2:
        return []
    batches = [p[1] for p in pending]
    apply_cross_section_ranks(batches, forward_days, variant)
    for (sid, _feats, label), feats in zip(pending, batches):
        y = float(label) if label is not None else 0.0
        out.append(
            LabeledSample(
                stock_id=sid,
                feature_date=pred_date,
                feature_idx=fi,
                x=vectorize(feats, forward_days, variant),
                y=y,
            )
        )
    return out


def _collect_train_samples(
    by_code: dict[str, list],
    code_to_id: dict[str, int],
    dates: list[str],
    ctx: MlFeatureContext,
    forward_days: int,
    train_start_idx: int,
    train_end_idx: int,
    test_feature_idx: int,
    variant: str = "v2",
) -> list[LabeledSample]:
    lookback = LOOKBACK_BY_HORIZON.get(forward_days, 30)
    min_bars = _min_bars(forward_days)
    train_dates = set(dates[train_start_idx : train_end_idx + 1])
    pending: dict[str, list[tuple[str, int, int, float]]] = defaultdict(list)

    for code, series in by_code.items():
        sid = code_to_id.get(code)
        if not sid or len(series) < min_bars:
            continue
        for i in range(lookback, len(series) - forward_days):
            dt = series[i][0]
            if dt not in train_dates:
                continue
            close = series[i][1]
            fwd = series[i + forward_days][1]
            label = (fwd / close - 1) if close > 0 else 0.0
            pending[dt].append((code, sid, i, label))

    d_idx = _date_to_idx(dates)
    samples: list[LabeledSample] = []
    for dt in sorted(pending.keys()):
        batch: list[dict] = []
        meta: list[tuple[int, float]] = []
        for code, sid, i, label in pending[dt]:
            feats = compute_base_features(by_code[code], i, forward_days, sid, ctx, variant)
            batch.append(feats)
            meta.append((sid, label))
        if len(batch) < 2:
            continue
        apply_cross_section_ranks(batch, forward_days, variant)
        fi = d_idx[dt]
        for feats, (sid, lab) in zip(batch, meta):
            samples.append(
                LabeledSample(
                    stock_id=sid,
                    feature_date=dt,
                    feature_idx=fi,
                    x=vectorize(feats, forward_days, variant),
                    y=float(lab),
                )
            )

    feature_indices = [s.feature_idx for s in samples]
    mask = purge_train_mask(
        feature_indices,
        forward_days,
        test_feature_idx,
        embargo_days=WF_EMBARGO_DAYS,
    )
    return [s for s, ok in zip(samples, mask) if ok]


def _group_sizes(samples: list[LabeledSample]) -> list[int]:
    """按 feature_idx 分组，返回每组样本数（LambdaRank 用）。"""
    if not samples:
        return []
    groups = []
    current = samples[0].feature_idx
    count = 1
    for s in samples[1:]:
        if s.feature_idx == current:
            count += 1
        else:
            groups.append(count)
            current = s.feature_idx
            count = 1
    groups.append(count)
    return groups


def _quintile_labels(samples: list[LabeledSample]) -> list[int]:
    """把每个交易日内的收益转成 1-5 quintile 整数标签（LambdaRank 用）。"""
    if not samples:
        return []
    from collections import defaultdict

    groups: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for idx, s in enumerate(samples):
        groups[s.feature_idx].append((idx, s.y))
    labels = [0] * len(samples)
    for idxs_vals in groups.values():
        idxs, vals = zip(*idxs_vals)
        n = len(vals)
        if n == 0:
            continue
        sorted_pos = sorted(range(n), key=lambda i: vals[i])
        for rank, pos in enumerate(sorted_pos):
            quintile = min(5, max(1, int(rank / n * 5) + 1))
            labels[idxs[pos]] = quintile
    return labels


def _fit_model(
    X_fit,
    y_fit,
    X_valid,
    y_valid,
    feat_names: list[str],
    fit_samples: list[LabeledSample] | None = None,
    valid_samples: list[LabeledSample] | None = None,
    variant: str = "v2",
):
    import numpy as np

    mode = "lightgbm"
    try:
        import lightgbm as lgb

        if variant == "v4":
            fit_group = _group_sizes(fit_samples or [])
            valid_group = _group_sizes(valid_samples or [])
            fit_labels = np.array(_quintile_labels(fit_samples or []), dtype=np.int32)
            valid_labels = np.array(_quintile_labels(valid_samples or []), dtype=np.int32)
            train_data = lgb.Dataset(
                X_fit, label=fit_labels, group=fit_group, feature_name=feat_names
            )
            valid_data = lgb.Dataset(
                X_valid, label=valid_labels, group=valid_group, feature_name=feat_names
            )
            params = {
                "objective": "lambdarank",
                "metric": "ndcg",
                "lambdarank_truncation_level": 10,
                "verbosity": -1,
                "num_leaves": 15,
                "max_depth": 5,
                "learning_rate": 0.02,
                "lambda_l1": 1.0,
                "lambda_l2": 1.0,
                "feature_fraction": 0.6,
                "bagging_fraction": 0.7,
                "bagging_freq": 1,
                "min_data_in_leaf": 50,
            }
        else:
            train_data = lgb.Dataset(X_fit, label=y_fit, feature_name=feat_names)
            valid_data = lgb.Dataset(X_valid, label=y_valid, feature_name=feat_names)
            params = {
                "objective": "regression",
                "metric": "l2",
                "verbosity": -1,
                "num_leaves": 31,
                "learning_rate": 0.05,
                "lambda_l1": 0.1,
                "lambda_l2": 0.1,
                "feature_fraction": 0.8,
                "bagging_fraction": 0.8,
                "bagging_freq": 1,
            }

        model = lgb.train(
            params,
            train_data,
            num_boost_round=200,
            valid_sets=[valid_data],
            callbacks=[lgb.early_stopping(stopping_rounds=30, verbose=False)],
        )
        imp = dict(zip(feat_names, model.feature_importance(importance_type="gain").tolist()))
        return model, mode, imp
    except Exception:
        from sklearn.linear_model import Ridge

        model = Ridge(alpha=1.0).fit(X_fit, y_fit)
        mode = "ridge"
        return model, mode, {}


def _pred_to_score(raw: float) -> float:
    return round(max(0, min(100, 50 + float(raw) * 500)), 2)


def run_h20_walkforward(
    db_path: str,
    *,
    train_window_days: int = WF_TRAIN_WINDOW_DAYS,
    step_days: int = WF_STEP_DAYS,
    forward_days: int = 20,
    max_folds: int | None = 12,
    variant: str = "v2",
) -> dict[str, Any]:
    by_code, code_to_id, dates = load_quote_panel(db_path)
    if len(dates) < WF_MIN_HISTORY_DAYS:
        return {
            "status": "error",
            "reason": "insufficient_history",
            "need_days": WF_MIN_HISTORY_DAYS,
            "have_days": len(dates),
        }

    conn = sqlite3.connect(db_path)
    configure_sqlite_conn(conn)
    ctx = MlFeatureContext.load(conn, dates)
    ensure_ml_validation_tables(conn)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS ml_predictions (
            stock_id INTEGER NOT NULL, pred_date TEXT NOT NULL,
            score REAL NOT NULL, model_version TEXT NOT NULL DEFAULT 'v0',
            PRIMARY KEY (stock_id, pred_date, model_version)
        )"""
    )

    feat_names = feature_names_for(forward_days, variant)
    folds_run = 0
    fold_metrics: list[dict] = []
    windows = list(
        iter_walkforward_windows(
            dates,
            train_window_days=train_window_days,
            step_days=step_days,
            horizon=forward_days,
            embargo_days=WF_EMBARGO_DAYS,
        )
    )
    if max_folds is not None and len(windows) > max_folds:
        windows = windows[-max_folds:]

    for win in windows:
        train_samples = _collect_train_samples(
            by_code,
            code_to_id,
            dates,
            ctx,
            forward_days,
            win.train_start_idx,
            win.train_end_idx,
            win.test_feature_idx,
            variant=variant,
        )
        if len(train_samples) < MIN_TRAIN_SAMPLES:
            continue

        sorted_fi = sorted({s.feature_idx for s in train_samples})
        valid_start = sorted_fi[int(len(sorted_fi) * 0.85)] if sorted_fi else win.train_end_idx
        fit_fi, valid_fi = split_valid_purged(
            sorted_fi,
            forward_days,
            valid_start,
            embargo_days=WF_EMBARGO_DAYS,
        )
        fit_set = {fi for fi in fit_fi}
        valid_set = {fi for fi in valid_fi}
        fit_samples = [s for s in train_samples if s.feature_idx in fit_set]
        valid_samples = [s for s in train_samples if s.feature_idx in valid_set]
        if len(fit_samples) < 50 or len(valid_samples) < 20:
            continue

        import numpy as np

        X_fit = np.array([s.x for s in fit_samples], dtype=np.float32)
        y_fit = np.array([s.y for s in fit_samples], dtype=np.float32)
        X_valid = np.array([s.x for s in valid_samples], dtype=np.float32)
        y_valid = np.array([s.y for s in valid_samples], dtype=np.float32)

        model, mode, imp = _fit_model(
            X_fit, y_fit, X_valid, y_valid, feat_names,
            fit_samples=fit_samples, valid_samples=valid_samples, variant=variant,
        )
        train_rmse = rmse(model.predict(X_fit), y_fit.tolist()) if variant != "v4" else None

        oos = _build_cross_section(
            by_code,
            code_to_id,
            dates,
            ctx,
            win.test_date,
            forward_days,
            with_labels=True,
            variant=variant,
        )
        if len(oos) < 10:
            continue
        X_oos = np.array([s.x for s in oos], dtype=np.float32)
        raw_preds = model.predict(X_oos)
        labels = [s.y for s in oos]
        preds = raw_preds.tolist()
        rank_ic = spearman_rank_ic(preds, labels)
        ic = pearson_ic(preds, labels)
        ls = long_short_return(preds, labels)

        mv = f"{mode}_h{forward_days}_wf_{variant}"
        insert_train_run(
            conn,
            {
                "horizon": forward_days,
                "train_start": win.train_start,
                "train_end": win.train_end,
                "test_start": win.test_date,
                "test_end": win.label_end_date,
                "model_version": mv,
                "oos_rank_ic": rank_ic,
                "oos_ic": ic,
                "oos_long_short_return": ls,
                "feature_importance": imp,
                "train_rmse": train_rmse,
                "n_oos": len(oos),
                "fold": win.fold,
            },
        )
        for s, raw in zip(oos, preds):
            upsert_oos_daily(
                conn,
                horizon=forward_days,
                pred_date=win.test_date,
                stock_id=s.stock_id,
                pred_raw=float(raw),
                pred_score=_pred_to_score(raw),
                label=s.y,
                model_version=mv,
                fold=win.fold,
            )
        fold_metrics.append(
            {
                "fold": win.fold,
                "test_date": win.test_date,
                "oos_rank_ic": rank_ic,
                "n_oos": len(oos),
            }
        )
        folds_run += 1

    # 生产截面：全历史 purged 训练 + 最新日预测
    live_mv = f"lightgbm_h{forward_days}_wf_live"
    if len(dates) >= train_window_days + forward_days:
        last_win_train_end = len(dates) - 1 - forward_days - WF_EMBARGO_DAYS - 1
        train_start_idx = max(0, last_win_train_end - train_window_days + 1)
        pseudo_test = len(dates) - 1
        all_train = _collect_train_samples(
            by_code,
            code_to_id,
            dates,
            ctx,
            forward_days,
            train_start_idx,
            last_win_train_end,
            pseudo_test,
            variant=variant,
        )
        if len(all_train) >= MIN_TRAIN_SAMPLES:
            sorted_fi = sorted({s.feature_idx for s in all_train})
            valid_start = sorted_fi[int(len(sorted_fi) * 0.85)]
            fit_fi, valid_fi = split_valid_purged(
                sorted_fi, forward_days, valid_start, embargo_days=WF_EMBARGO_DAYS
            )
            fit_samples = [s for s in all_train if s.feature_idx in set(fit_fi)]
            valid_samples = [s for s in all_train if s.feature_idx in set(valid_fi)]
            if len(fit_samples) >= 50 and len(valid_samples) >= 20:
                import numpy as np

                model, mode, _ = _fit_model(
                    np.array([s.x for s in fit_samples], dtype=np.float32),
                    np.array([s.y for s in fit_samples], dtype=np.float32),
                    np.array([s.x for s in valid_samples], dtype=np.float32),
                    np.array([s.y for s in valid_samples], dtype=np.float32),
                    feat_names,
                    fit_samples=fit_samples,
                    valid_samples=valid_samples,
                    variant=variant,
                )
                live_mv = f"{mode}_h{forward_days}_wf_live"
                pred_date = dates[-1]
                live = _build_cross_section(
                    by_code, code_to_id, dates, ctx, pred_date, forward_days,
                    with_labels=False, variant=variant,
                )
                if live:
                    X_live = np.array([s.x for s in live], dtype=np.float32)
                    for s, raw in zip(live, model.predict(X_live)):
                        conn.execute(
                            "INSERT OR REPLACE INTO ml_predictions VALUES (?,?,?,?)",
                            (s.stock_id, pred_date, _pred_to_score(raw), live_mv),
                        )

    conn.commit()
    conn.close()

    if folds_run < MIN_FOLDS:
        return {
            "status": "error",
            "reason": "insufficient_wf_folds",
            "folds_completed": folds_run,
            "need_folds": MIN_FOLDS,
            "fold_metrics": fold_metrics,
        }

    mean_rank_ic = (
        sum(f["oos_rank_ic"] or 0 for f in fold_metrics) / len(fold_metrics) if fold_metrics else None
    )
    return {
        "status": "done",
        "mode": "walkforward_h20",
        "forward_days": forward_days,
        "folds_completed": folds_run,
        "mean_oos_rank_ic": round(mean_rank_ic, 4) if mean_rank_ic is not None else None,
        "fold_metrics": fold_metrics[-5:],
        "live_model_version": live_mv,
        "pred_date": dates[-1] if dates else None,
        "train_window_days": train_window_days,
    }
