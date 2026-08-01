"""P3-E：Jump Model 四格 regime — jumpmodels 优先，3.9 回退 SimpleJumpModel。"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Optional

import numpy as np

import config
from services.market_regime import REGIME_BUCKET_ORDER, regime_bucket_label
from services.regime_hmm import (
    FEATURE_NAMES,
    _compare_verdict,
    load_hmm_features,
    map_states_to_buckets,
)
from services.regime_validation import (
    compute_dwell_times,
    index_returns_from_kline,
    internal_consistency_report,
    load_regime_rows,
)

JUMP_MODEL_VERSION = "jump_model_v1"
JUMP_DAILY_MODEL_VERSION = "jump_dynamic_wf_v1"
N_STATES = 4
DEFAULT_PENALTIES = (25.0, 50.0, 75.0, 100.0)
WF_TRAIN_DAYS = 500
WF_VAL_DAYS = 60
WF_STEP_DAYS = 20
WF_LAMBDA_CANDIDATES = tuple(range(5, 45, 5))  # 5..40 step 5


@dataclass
class JumpFitResult:
    jump_penalty: float
    backend: str
    model: Any
    state_to_bucket: dict[int, str]
    state_stats: list[dict[str, Any]]
    feature_means: list[float]
    feature_stds: list[float]


def _standardize_fit(X: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd = np.where(sd < 1e-9, 1.0, sd)
    return (X - mu) / sd, mu, sd


def _standardize_apply(X: np.ndarray, mu: np.ndarray, sd: np.ndarray) -> np.ndarray:
    return (X - mu) / sd


def _state_feature_stats(X: np.ndarray, states: np.ndarray) -> list[dict[str, Any]]:
    out = []
    for s in range(N_STATES):
        mask = states == s
        if not mask.any():
            out.append({
                "state": s, "count": 0,
                "ret20": 0.0, "vol20": 0.0, "adx": 0.0, "pv_ma60": 0.0,
            })
            continue
        chunk = X[mask]
        out.append({
            "state": s,
            "count": int(mask.sum()),
            "ret20": float(chunk[:, 0].mean()),
            "vol20": float(chunk[:, 1].mean()),
            "adx": float(chunk[:, 2].mean()),
            "pv_ma60": float(chunk[:, 4].mean()),
        })
    return out


def _map_states_to_buckets(state_stats: list[dict[str, Any]]) -> dict[int, str]:
    adapted = [{"state": s["state"], **s} for s in state_stats]
    return map_states_to_buckets(adapted)


def _viterbi_jump(dist: np.ndarray, jump_penalty: float) -> np.ndarray:
    """DP：最小化距离 + λ·切换次数。"""
    t_len, k_len = dist.shape
    dp = np.full((t_len, k_len), np.inf)
    prev = np.zeros((t_len, k_len), dtype=int)
    dp[0] = dist[0]
    for t in range(1, t_len):
        for k in range(k_len):
            best_val = np.inf
            best_j = 0
            for j in range(k_len):
                pen = 0.0 if j == k else jump_penalty
                v = dp[t - 1, j] + pen + dist[t, k]
                if v < best_val:
                    best_val = v
                    best_j = j
            dp[t, k] = best_val
            prev[t, k] = best_j
    states = np.zeros(t_len, dtype=int)
    states[-1] = int(np.argmin(dp[-1]))
    for t in range(t_len - 2, -1, -1):
        states[t] = prev[t + 1, states[t + 1]]
    return states


class SimpleJumpModel:
    """轻量 Jump Model（DP + 质心迭代），Python 3.9 回退。"""

    def __init__(
        self,
        n_components: int = N_STATES,
        jump_penalty: float = 50.0,
        random_state: int = 42,
        max_iter: int = 50,
    ):
        self.n_components = n_components
        self.jump_penalty = jump_penalty
        self.random_state = random_state
        self.max_iter = max_iter
        self.centers_: np.ndarray | None = None
        self.labels_: np.ndarray | None = None

    def _dist_matrix(self, X: np.ndarray) -> np.ndarray:
        assert self.centers_ is not None
        return np.sum((X[:, None, :] - self.centers_[None, :, :]) ** 2, axis=2)

    def fit(self, X: np.ndarray) -> SimpleJumpModel:
        from sklearn.cluster import KMeans

        km = KMeans(
            n_clusters=self.n_components,
            n_init=10,
            random_state=self.random_state,
        )
        labels = km.fit_predict(X)
        self.centers_ = km.cluster_centers_.copy()
        for _ in range(self.max_iter):
            dist = self._dist_matrix(X)
            labels = _viterbi_jump(dist, self.jump_penalty)
            new_centers = np.zeros_like(self.centers_)
            for k in range(self.n_components):
                mask = labels == k
                if mask.any():
                    new_centers[k] = X[mask].mean(axis=0)
                else:
                    new_centers[k] = self.centers_[k]
            if np.allclose(new_centers, self.centers_, atol=1e-6):
                break
            self.centers_ = new_centers
        self.labels_ = labels
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        if self.centers_ is None:
            raise RuntimeError("model not fitted")
        return _viterbi_jump(self._dist_matrix(X), self.jump_penalty)


class _JumpModelsWrapper:
    """jumpmodels 官方库封装（需 Python ≥3.10）。"""

    def __init__(self, n_components: int, jump_penalty: float, random_state: int):
        from jumpmodels.jump import JumpModel

        self._inner = JumpModel(
            n_components=n_components,
            jump_penalty=float(jump_penalty),
            random_state=random_state,
        )
        self.labels_: np.ndarray | None = None

    def fit(self, X: np.ndarray) -> _JumpModelsWrapper:
        import pandas as pd

        df = pd.DataFrame(X, columns=list(FEATURE_NAMES))
        self._inner.fit(df)
        self.labels_ = np.asarray(self._inner.predict(df), dtype=int)
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        import pandas as pd

        df = pd.DataFrame(X, columns=list(FEATURE_NAMES))
        return np.asarray(self._inner.predict(df), dtype=int)


def jumpmodels_available() -> bool:
    try:
        from jumpmodels.jump import JumpModel  # noqa: F401
        import pandas as pd
        import numpy as np

        rng = np.random.default_rng(0)
        df = pd.DataFrame(rng.normal(size=(30, 5)), columns=list("abcde"))
        m = JumpModel(n_components=2, jump_penalty=1.0, random_state=0)
        m.fit(df)
        return True
    except Exception:
        return False


def _make_jump_model(
    jump_penalty: float,
    *,
    n_states: int = N_STATES,
    random_state: int = 42,
    backend: Literal["auto", "jumpmodels", "simple"] = "auto",
) -> tuple[Any, str]:
    use_lib = backend == "jumpmodels" or (backend == "auto" and jumpmodels_available())
    if use_lib:
        try:
            return _JumpModelsWrapper(n_states, jump_penalty, random_state), "jumpmodels"
        except Exception:
            pass
    return SimpleJumpModel(n_states, jump_penalty, random_state), "simple_dp"


def fit_jump(
    X: np.ndarray,
    *,
    jump_penalty: float = 50.0,
    n_states: int = N_STATES,
    random_state: int = 42,
    backend: Literal["auto", "jumpmodels", "simple"] = "auto",
) -> JumpFitResult:
    Xs, mu, sd = _standardize_fit(X)
    model, backend_name = _make_jump_model(
        jump_penalty, n_states=n_states, random_state=random_state, backend=backend,
    )
    model.fit(Xs)
    labels = model.predict(Xs)
    raw_stats = _state_feature_stats(X, labels)
    state_to_bucket = _map_states_to_buckets(raw_stats)
    enriched = [{**st, "bucket": state_to_bucket.get(st["state"], "oscillation")} for st in raw_stats]
    return JumpFitResult(
        jump_penalty=jump_penalty,
        backend=backend_name,
        model=model,
        state_to_bucket=state_to_bucket,
        state_stats=enriched,
        feature_means=mu.tolist(),
        feature_stds=sd.tolist(),
    )


def predict_jump_buckets(
    fit: JumpFitResult,
    dates: list[str],
    X: np.ndarray,
) -> list[dict[str, Any]]:
    mu = np.asarray(fit.feature_means)
    sd = np.asarray(fit.feature_stds)
    Xs = _standardize_apply(X, mu, sd)
    states = fit.model.predict(Xs)
    rows = []
    for i, td in enumerate(dates):
        st = int(states[i])
        bucket = fit.state_to_bucket.get(st, "oscillation")
        rows.append({
            "trade_date": td,
            "jump_state": st,
            "regime_bucket": bucket,
            "regime_bucket_label": regime_bucket_label(bucket),
            "jump_penalty": fit.jump_penalty,
            "backend": fit.backend,
        })
    return rows


def jump_rows_as_regime_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"trade_date": r["trade_date"], "bucket": r["regime_bucket"]} for r in rows]


def _single_penalty_report(
    *,
    jump_penalty: float,
    dates: list[str],
    X: np.ndarray,
    split: int,
    test_dates: list[str],
    rule_rows: list[dict[str, Any]],
    rule_by_date: dict[str, str | None],
    index_returns: dict[str, float],
    backend: Literal["auto", "jumpmodels", "simple"],
) -> dict[str, Any]:
    fit = fit_jump(X[:split], jump_penalty=jump_penalty, backend=backend)
    all_rows = predict_jump_buckets(fit, dates, X)
    test_rows = [r for r in all_rows if r["trade_date"] in set(test_dates)]

    agree = total = 0
    for r in test_rows:
        rb = rule_by_date.get(r["trade_date"])
        if rb:
            total += 1
            if rb == r["regime_bucket"]:
                agree += 1

    dist: dict[str, int] = {b: 0 for b in REGIME_BUCKET_ORDER}
    for r in all_rows:
        b = r.get("regime_bucket")
        if b in dist:
            dist[b] += 1

    regime_rows = jump_rows_as_regime_rows(all_rows)
    buckets = [r["bucket"] for r in regime_rows if r.get("bucket") in REGIME_BUCKET_ORDER]
    rule_buckets = [r["bucket"] for r in rule_rows if r.get("bucket") in REGIME_BUCKET_ORDER]

    dwell = compute_dwell_times(buckets)
    rule_dwell = compute_dwell_times(rule_buckets)
    internal = internal_consistency_report(regime_rows, index_returns)
    rule_internal = internal_consistency_report(rule_rows, index_returns)

    return {
        "jump_penalty": jump_penalty,
        "backend": fit.backend,
        "state_mapping": fit.state_stats,
        "state_to_bucket": fit.state_to_bucket,
        "distribution": dist,
        "oos_bucket_agreement_pct": round(agree / total * 100, 1) if total else None,
        "oos_samples": total,
        "dwell_time": dwell,
        "internal": {
            "return_anova_p": internal.get("return_anova", {}).get("p_value"),
            "significant": internal.get("return_anova", {}).get("significant_05"),
            "verdict": internal.get("verdict"),
        },
        "verdict": _compare_verdict(
            agree, total, dwell, rule_dwell, internal, rule_internal,
        ).replace("HMM", f"JM(λ={jump_penalty:g})"),
        "_fit": fit,
    }


def compare_jump_vs_rules(
    conn: sqlite3.Connection,
    *,
    days: int = 730,
    train_ratio: float = 0.85,
    penalties: tuple[float, ...] = DEFAULT_PENALTIES,
    backend: Literal["auto", "jumpmodels", "simple"] = "auto",
) -> dict[str, Any]:
    """Jump Model 多 λ 扫描 vs 规则 L1；训练集拟合，OOS 评估。"""
    dates, X = load_hmm_features(conn, days=days)
    if len(dates) < 80:
        return {"error": "特征样本不足（需 market_regime_daily ≥80 天）"}

    split = int(len(dates) * train_ratio)
    if split < 60:
        split = len(dates) - 30
    test_dates = dates[split:]

    rule_rows = load_regime_rows(conn, primary="csi800", days=days)
    rule_by_date = {r["trade_date"]: r.get("bucket") for r in rule_rows}

    from services.market_index import fetch_index_kline

    kline = fetch_index_kline(
        config.REGIME_INDEX_CSI800,
        period="daily",
        days=min(days + 120, 800),
        with_technical=False,
    )
    index_returns = index_returns_from_kline(kline.get("kline") or [])

    rule_dist: dict[str, int] = {b: 0 for b in REGIME_BUCKET_ORDER}
    for r in rule_rows:
        b = r.get("bucket")
        if b in rule_dist:
            rule_dist[b] += 1
    rule_dwell = compute_dwell_times(
        [r["bucket"] for r in rule_rows if r.get("bucket") in REGIME_BUCKET_ORDER],
    )

    penalty_reports: dict[str, Any] = {}
    best_key: str | None = None
    best_score: float = -1.0
    for lam in penalties:
        key = str(lam)
        rep = _single_penalty_report(
            jump_penalty=lam,
            dates=dates,
            X=X,
            split=split,
            test_dates=test_dates,
            rule_rows=rule_rows,
            rule_by_date=rule_by_date,
            index_returns=index_returns,
            backend=backend,
        )
        rep.pop("_fit", None)
        penalty_reports[key] = rep
        dwell = rep.get("dwell_time", {}).get("overall_mean_days") or 0
        oos = rep.get("oos_bucket_agreement_pct") or 0
        score = float(dwell) * 0.6 + float(oos) * 0.4
        if score > best_score:
            best_score = score
            best_key = key

    return {
        "generated_at": date.today().isoformat(),
        "model_version": JUMP_MODEL_VERSION,
        "feature_names": list(FEATURE_NAMES),
        "jumpmodels_available": jumpmodels_available(),
        "backend_requested": backend,
        "sample_days": len(dates),
        "train_days": split,
        "test_days": len(test_dates),
        "start_date": dates[0],
        "end_date": dates[-1],
        "penalties": list(penalties),
        "distribution_rules": rule_dist,
        "dwell_time_rules": rule_dwell,
        "penalty_reports": penalty_reports,
        "recommended_penalty": float(best_key) if best_key else None,
        "note": "Jump Model 为对照层；生产 L1/L2/L3 仍用规则 + persistence",
    }


def persist_jump_buckets(
    conn: sqlite3.Connection,
    rows: list[dict[str, Any]],
    *,
    index_code: str | None = None,
    model_version: str = JUMP_MODEL_VERSION,
) -> int:
    idx = index_code or config.REGIME_INDEX_CSI800
    n = 0
    for r in rows:
        conn.execute(
            """INSERT OR REPLACE INTO market_regime_jump_daily
               (trade_date, index_code, jump_state, regime_bucket, jump_penalty,
                backend, model_version, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (
                r["trade_date"],
                idx,
                r.get("jump_state"),
                r.get("regime_bucket"),
                r.get("jump_penalty"),
                r.get("backend"),
                model_version,
            ),
        )
        n += 1
    conn.commit()
    return n


def fit_and_persist_full_sample(
    conn: sqlite3.Connection,
    *,
    days: int = 730,
    jump_penalty: float = 50.0,
    backend: Literal["auto", "jumpmodels", "simple"] = "auto",
) -> dict[str, Any]:
    dates, X = load_hmm_features(conn, days=days)
    if len(dates) < 60:
        return {"error": "样本不足", "persisted": 0}
    fit = fit_jump(X, jump_penalty=jump_penalty, backend=backend)
    rows = predict_jump_buckets(fit, dates, X)
    n = persist_jump_buckets(conn, rows)
    return {
        "persisted": n,
        "jump_penalty": jump_penalty,
        "backend": fit.backend,
        "model_version": JUMP_MODEL_VERSION,
        "state_to_bucket": fit.state_to_bucket,
        "state_stats": fit.state_stats,
        "distribution": {
            b: sum(1 for r in rows if r["regime_bucket"] == b) for b in REGIME_BUCKET_ORDER
        },
    }


def format_compare_report_text(report: dict[str, Any]) -> str:
    if report.get("error"):
        return f"错误: {report['error']}"

    lines = [
        "📊 Jump Model vs 规则 L1 对照",
        "━" * 48,
        f"特征: {', '.join(report.get('feature_names') or FEATURE_NAMES)}",
        f"jumpmodels 可用: {report.get('jumpmodels_available')}",
        f"样本: {report.get('start_date')} → {report.get('end_date')} ({report.get('sample_days')} 天)",
        f"训练/测试: {report.get('train_days')} / {report.get('test_days')} 天",
        f"规则分布: {report.get('distribution_rules')}",
        f"规则停留: {report.get('dwell_time_rules', {}).get('overall_mean_days')} 天",
        f"推荐 λ: {report.get('recommended_penalty')}",
        "",
    ]
    for key, mr in sorted(
        (report.get("penalty_reports") or {}).items(),
        key=lambda kv: float(kv[0]),
    ):
        lines.extend([
            f"── λ = {key} ({mr.get('backend')}) ──",
            f"OOS 桶一致率: {mr.get('oos_bucket_agreement_pct')}% (n={mr.get('oos_samples')})",
            "状态 → 四格:",
        ])
        for st in mr.get("state_mapping") or []:
            lines.append(
                f"  state {st['state']} → {st.get('bucket')} "
                f"(n={st.get('count')}, ret20={st.get('ret20', 0):+.3f}, vol={st.get('vol20', 0):.3f})"
            )
        lines.extend([
            f"分布: {mr.get('distribution')}",
            f"停留: {mr.get('dwell_time', {}).get('overall_mean_days')} 天 · "
            f"切换 {mr.get('dwell_time', {}).get('total_transitions')} 次",
            f"内部: {(mr.get('internal') or {}).get('verdict', '')[:70]}",
            mr.get("verdict", ""),
            "",
        ])
    lines.append(report.get("note", ""))
    return "\n".join(lines)


def _score_lambda_window(
    fit: JumpFitResult,
    val_dates: list[str],
    X_val: np.ndarray,
    rule_by_date: dict[str, str | None],
) -> dict[str, Any]:
    """在验证窗上评估单个 λ（fit 已在训练窗完成）。"""
    val_rows = predict_jump_buckets(fit, val_dates, X_val)
    agree = total = 0
    jm_buckets: list[str] = []
    for r in val_rows:
        b = r["regime_bucket"]
        if b in REGIME_BUCKET_ORDER:
            jm_buckets.append(b)
        rb = rule_by_date.get(r["trade_date"])
        if rb:
            total += 1
            if rb == b:
                agree += 1

    rule_buckets_val = [
        rule_by_date[d] for d in val_dates
        if rule_by_date.get(d) in REGIME_BUCKET_ORDER
    ]
    rule_dwell = compute_dwell_times(rule_buckets_val).get("overall_mean_days") or 36.5
    jm_dwell = compute_dwell_times(jm_buckets).get("overall_mean_days") or 0.0
    target = max(float(rule_dwell), 1.0)
    consistency = (agree / total) if total else 0.0
    dwell_norm = min(float(jm_dwell) / target, 1.0)
    score = consistency * 0.5 + dwell_norm * 0.5

    return {
        "jump_penalty": fit.jump_penalty,
        "score": round(score, 4),
        "consistency": round(consistency, 4),
        "consistency_pct": round(consistency * 100, 1),
        "dwell_mean": round(float(jm_dwell), 1),
        "rule_dwell_mean": round(float(rule_dwell), 1),
        "dwell_norm": round(dwell_norm, 4),
        "oos_samples": total,
        "backend": fit.backend,
    }


def _pick_best_lambda(
    X_train: np.ndarray,
    val_dates: list[str],
    X_val: np.ndarray,
    rule_by_date: dict[str, str | None],
    *,
    candidates: tuple[float, ...] = WF_LAMBDA_CANDIDATES,
    backend: Literal["auto", "jumpmodels", "simple"] = "simple",
) -> tuple[float, dict[str, Any], list[dict[str, Any]]]:
    best_lam = candidates[0]
    best_score = -1.0
    best_detail: dict[str, Any] = {}
    trials: list[dict[str, Any]] = []
    for lam in candidates:
        fit = fit_jump(X_train, jump_penalty=float(lam), backend=backend)
        detail = _score_lambda_window(fit, val_dates, X_val, rule_by_date)
        trials.append(detail)
        if detail["score"] > best_score:
            best_score = detail["score"]
            best_lam = float(lam)
            best_detail = detail
    return best_lam, best_detail, trials


def walkforward_tune_lambda(
    conn: sqlite3.Connection,
    *,
    days: int = 730,
    train_days: int = WF_TRAIN_DAYS,
    val_days: int = WF_VAL_DAYS,
    step_days: int = WF_STEP_DAYS,
    candidates: tuple[float, ...] = WF_LAMBDA_CANDIDATES,
    backend: Literal["auto", "jumpmodels", "simple"] = "simple",
) -> dict[str, Any]:
    """滚动 Walk-Forward 选 λ；选中 λ 应用于验证窗之后的 step 天（无前视）。"""
    requested_train_days = train_days
    dates, X = load_hmm_features(conn, days=days)
    n = len(dates)
    effective_train = train_days
    min_need = effective_train + val_days + step_days
    if n < min_need:
        effective_train = max(120, n - val_days - step_days - 5)
        min_need = effective_train + val_days + step_days
    if n < min_need:
        return {
            "error": (
                f"样本不足：需 ≥{requested_train_days + val_days + step_days} 天"
                f"（或自适应后 ≥{min_need}），当前 {n}"
            ),
        }
    train_days = effective_train

    rule_rows = load_regime_rows(conn, primary="csi800", days=days)
    rule_by_date = {r["trade_date"]: r.get("bucket") for r in rule_rows}

    windows: list[dict[str, Any]] = []
    daily_lambda: dict[str, float] = {}

    i = 0
    n = len(dates)
    while i + train_days + val_days <= n:
        tr0, tr1 = i, i + train_days
        va0, va1 = tr1, tr1 + val_days
        train_dates = dates[tr0:tr1]
        val_dates = dates[va0:va1]
        X_train, X_val = X[tr0:tr1], X[va0:va1]

        best_lam, best_detail, trials = _pick_best_lambda(
            X_train, val_dates, X_val, rule_by_date,
            candidates=candidates, backend=backend,
        )

        apply0 = va1
        apply1 = min(va1 + step_days, n)
        apply_dates = dates[apply0:apply1]

        win = {
            "train_start": train_dates[0],
            "train_end": train_dates[-1],
            "val_start": val_dates[0],
            "val_end": val_dates[-1],
            "apply_start": apply_dates[0] if apply_dates else None,
            "apply_end": apply_dates[-1] if apply_dates else None,
            "best_lambda": best_lam,
            "best_score": best_detail.get("score"),
            "val_consistency_pct": best_detail.get("consistency_pct"),
            "val_dwell_mean": best_detail.get("dwell_mean"),
            "val_rule_dwell_mean": best_detail.get("rule_dwell_mean"),
            "backend": best_detail.get("backend"),
            "trials": trials,
        }
        windows.append(win)

        for d in apply_dates:
            daily_lambda[d] = best_lam

        i += step_days

    timeline = [
        {"trade_date": d, "jump_penalty": daily_lambda[d]}
        for d in sorted(daily_lambda)
    ]

    lam_values = [w["best_lambda"] for w in windows]
    summary = {
        "lambda_min": min(lam_values) if lam_values else None,
        "lambda_max": max(lam_values) if lam_values else None,
        "lambda_mean": round(sum(lam_values) / len(lam_values), 2) if lam_values else None,
        "window_count": len(windows),
        "timeline_days": len(timeline),
    }

    return {
        "generated_at": date.today().isoformat(),
        "model_version": JUMP_MODEL_VERSION,
        "sample_days": n,
        "start_date": dates[0],
        "end_date": dates[-1],
        "train_days": train_days,
        "train_days_requested": requested_train_days,
        "train_days_adjusted": train_days != requested_train_days,
        "val_days": val_days,
        "step_days": step_days,
        "candidates": list(candidates),
        "backend": backend,
        "windows": windows,
        "timeline": timeline,
        "summary": summary,
    }


def persist_lambda_timeline(
    conn: sqlite3.Connection,
    report: dict[str, Any],
) -> int:
    """写入 jump_lambda_walkforward 表（按 apply 区间逐日）。"""
    n = 0
    for win in report.get("windows") or []:
        lam = win.get("best_lambda")
        if lam is None:
            continue
        apply_start = win.get("apply_start")
        apply_end = win.get("apply_end")
        if not apply_start:
            continue
        for row in report.get("timeline") or []:
            td = row["trade_date"]
            if apply_start <= td <= (apply_end or apply_start):
                conn.execute(
                    """INSERT OR REPLACE INTO jump_lambda_walkforward
                       (trade_date, jump_penalty, train_start, train_end,
                        val_start, val_end, window_score, consistency_pct,
                        dwell_mean, backend, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                    (
                        td,
                        lam,
                        win.get("train_start"),
                        win.get("train_end"),
                        win.get("val_start"),
                        win.get("val_end"),
                        win.get("best_score"),
                        win.get("val_consistency_pct"),
                        win.get("val_dwell_mean"),
                        win.get("backend"),
                    ),
                )
                n += 1
    conn.commit()
    return n


def get_jump_penalty_for_date(
    conn: sqlite3.Connection,
    trade_date: str,
    *,
    default: float = 25.0,
) -> float:
    """读取当日 λ；无记录则取最近历史值。"""
    row = conn.execute(
        """SELECT jump_penalty FROM jump_lambda_walkforward
           WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT 1""",
        (trade_date,),
    ).fetchone()
    if row and row[0] is not None:
        return float(row[0])
    row2 = conn.execute(
        """SELECT jump_penalty FROM jump_lambda_walkforward
           ORDER BY trade_date ASC LIMIT 1""",
    ).fetchone()
    if row2 and row2[0] is not None:
        return float(row2[0])
    return default


def predict_jump_with_dynamic_lambda(
    conn: sqlite3.Connection,
    trade_date: str,
    *,
    days: int = 560,
    default_penalty: float = 25.0,
    backend: Literal["auto", "jumpmodels", "simple"] = "simple",
) -> dict[str, Any]:
    """用 WF 选出的 λ 对单日做 Jump 预测（训练窗截至 trade_date 之前）。"""
    lam = get_jump_penalty_for_date(conn, trade_date, default=default_penalty)
    all_dates, X = load_hmm_features(conn, days=days)
    if not all_dates or trade_date not in all_dates:
        return {"error": "无特征", "jump_penalty": lam}

    idx = all_dates.index(trade_date)
    if idx < 60:
        return {"error": "历史不足", "jump_penalty": lam}

    train_dates = all_dates[:idx]
    X_train = X[:idx]
    fit = fit_jump(X_train, jump_penalty=lam, backend=backend)
    row = predict_jump_buckets(fit, [trade_date], X[idx : idx + 1])[0]
    row["jump_penalty_source"] = "walkforward"
    return row


def sync_jump_regime_daily(
    conn: sqlite3.Connection,
    trade_date: str,
    *,
    backend: Literal["auto", "jumpmodels", "simple"] = "simple",
) -> dict[str, Any]:
    """15:30 日频：Walk-Forward 动态 λ + Jump 预测，写入 market_regime_jump_daily。"""
    result = predict_jump_with_dynamic_lambda(conn, trade_date, backend=backend)
    if result.get("error"):
        return {
            "trade_date": trade_date,
            "error": result["error"],
            "jump_penalty": result.get("jump_penalty"),
        }
    persist_jump_buckets(conn, [result], model_version=JUMP_DAILY_MODEL_VERSION)
    return {
        "trade_date": trade_date,
        "regime_bucket": result.get("regime_bucket"),
        "regime_bucket_label": result.get("regime_bucket_label"),
        "jump_penalty": result.get("jump_penalty"),
        "jump_penalty_source": result.get("jump_penalty_source"),
        "jump_state": result.get("jump_state"),
        "backend": result.get("backend"),
    }


def format_walkforward_report_text(report: dict[str, Any]) -> str:
    if report.get("error"):
        return f"错误: {report['error']}"
    s = report.get("summary") or {}
    lines = [
        "📈 Jump Model Walk-Forward λ 选参",
        "━" * 48,
        f"样本: {report.get('start_date')} → {report.get('end_date')} ({report.get('sample_days')} 天)",
        f"窗口: train={report.get('train_days')} val={report.get('val_days')} step={report.get('step_days')}",
        f"候选 λ: {report.get('candidates')}",
        f"窗口数: {s.get('window_count')} · 时间线覆盖: {s.get('timeline_days')} 天",
        f"λ 范围: {s.get('lambda_min')} ~ {s.get('lambda_max')} · 均值 {s.get('lambda_mean')}",
        "",
    ]
    for i, w in enumerate(report.get("windows") or [], 1):
        lines.append(
            f"[{i}] λ={w.get('best_lambda')} score={w.get('best_score')} "
            f"val一致={w.get('val_consistency_pct')}% "
            f"apply {w.get('apply_start')}→{w.get('apply_end')}"
        )
    return "\n".join(lines)
