"""P3-C：HMM 四状态市场 regime — 与规则 L1 并行对照（不替换生产链路）。"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

import numpy as np

import config
from services.market_regime import REGIME_BUCKET_ORDER, regime_bucket_label
from services.regime_validation import (
    compute_dwell_times,
    index_returns_from_kline,
    internal_consistency_report,
    load_regime_rows,
)

HMM_MODEL_VERSION = "gaussian_hmm_v1"
FEATURE_NAMES = ("ret20", "vol20", "adx", "ma20_slope", "pv_ma60")
N_STATES = 4


@dataclass
class HMMFitResult:
    model: Any
    state_to_bucket: dict[int, str]
    state_stats: list[dict[str, Any]]
    feature_means: list[float]
    feature_stds: list[float]


def _require_hmmlearn():
    try:
        from hmmlearn.hmm import GaussianHMM
    except ImportError as e:
        raise ImportError(
            "需要 hmmlearn：pip install hmmlearn>=0.3.2"
        ) from e
    return GaussianHMM


def load_hmm_features(
    conn: sqlite3.Connection,
    *,
    days: int = 730,
    index_code: str | None = None,
) -> tuple[list[str], np.ndarray]:
    """从 market_regime_daily 加载 CSI800 指数特征矩阵。"""
    idx = index_code or config.REGIME_INDEX_CSI800
    rows = conn.execute(
        """SELECT trade_date,
                  COALESCE(return_20d_csi800, return_20d) AS ret20,
                  COALESCE(volatility_20_csi800, volatility_20) AS vol20,
                  COALESCE(adx_csi800, adx) AS adx,
                  COALESCE(ma20_slope_csi800, ma20_slope) AS ma20_slope,
                  COALESCE(price_vs_ma60_csi800, price_vs_ma60) AS pv_ma60
           FROM market_regime_daily
           WHERE trade_date IS NOT NULL
           ORDER BY trade_date DESC LIMIT ?""",
        (days,),
    ).fetchall()
    if len(rows) < 60:
        return [], np.empty((0, len(FEATURE_NAMES)))

    ordered = list(reversed(rows))
    dates: list[str] = []
    matrix: list[list[float]] = []
    for r in ordered:
        vals = [float(r[i + 1]) if r[i + 1] is not None else np.nan for i in range(5)]
        if any(not np.isfinite(v) for v in vals):
            continue
        dates.append(str(r[0]))
        matrix.append(vals)

    if len(dates) < 60:
        return [], np.empty((0, len(FEATURE_NAMES)))

    X = np.asarray(matrix, dtype=float)
    return dates, X


def _standardize(X: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd = np.where(sd < 1e-9, 1.0, sd)
    return (X - mu) / sd, mu, sd


def _state_feature_stats(X: np.ndarray, states: np.ndarray) -> list[dict[str, Any]]:
    out = []
    for s in range(N_STATES):
        mask = states == s
        if not mask.any():
            out.append({"state": s, "count": 0, "ret20": 0.0, "vol20": 0.0, "adx": 0.0, "pv_ma60": 0.0})
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


def map_states_to_buckets(state_stats: list[dict[str, Any]]) -> dict[int, str]:
    """按状态质心语义映射四格 bucket（解决冲突时取次优）。"""
    stats = [s for s in state_stats if s.get("count", 0) > 0]
    all_state_ids = {int(s["state"]) for s in state_stats}
    if not stats:
        return {i: "oscillation" for i in all_state_ids} if all_state_ids else {i: "oscillation" for i in range(N_STATES)}

    mapping: dict[int, str] = {}
    used: set[int] = set()

    trend_up = max(stats, key=lambda s: s["ret20"] + s["pv_ma60"] * 0.5)
    mapping[trend_up["state"]] = "trend_up"
    used.add(trend_up["state"])

    avail = [s for s in stats if s["state"] not in used]
    if avail:
        trend_down = min(avail, key=lambda s: s["ret20"])
        mapping[trend_down["state"]] = "trend_down"
        used.add(trend_down["state"])

    avail = [s for s in stats if s["state"] not in used]
    if avail:
        high_vol = max(avail, key=lambda s: s["vol20"])
        mapping[high_vol["state"]] = "high_vol"
        used.add(high_vol["state"])

    for sid in all_state_ids:
        if sid not in mapping:
            mapping[sid] = "oscillation"

    return mapping


def fit_hmm(
    X: np.ndarray,
    *,
    n_states: int = N_STATES,
    n_iter: int = 100,
    random_state: int = 42,
) -> HMMFitResult:
    GaussianHMM = _require_hmmlearn()
    Xs, mu, sd = _standardize(X)

    model = GaussianHMM(
        n_components=n_states,
        covariance_type="diag",
        n_iter=n_iter,
        random_state=random_state,
        tol=1e-3,
    )
    model.fit(Xs)
    states = model.predict(Xs)
    raw_stats = _state_feature_stats(X, states)
    state_to_bucket = map_states_to_buckets(raw_stats)

    enriched = []
    for st in raw_stats:
        enriched.append({**st, "bucket": state_to_bucket.get(st["state"], "oscillation")})

    return HMMFitResult(
        model=model,
        state_to_bucket=state_to_bucket,
        state_stats=enriched,
        feature_means=mu.tolist(),
        feature_stds=sd.tolist(),
    )


def predict_buckets(
    fit: HMMFitResult,
    dates: list[str],
    X: np.ndarray,
) -> list[dict[str, Any]]:
    Xs = (X - np.asarray(fit.feature_means)) / np.asarray(fit.feature_stds)
    states = fit.model.predict(Xs)
    rows = []
    for i, td in enumerate(dates):
        st = int(states[i])
        bucket = fit.state_to_bucket.get(st, "oscillation")
        rows.append({
            "trade_date": td,
            "hmm_state": st,
            "regime_bucket": bucket,
            "regime_bucket_label": regime_bucket_label(bucket),
        })
    return rows


def persist_hmm_buckets(
    conn: sqlite3.Connection,
    rows: list[dict[str, Any]],
    *,
    index_code: str | None = None,
    model_version: str = HMM_MODEL_VERSION,
) -> int:
    idx = index_code or config.REGIME_INDEX_CSI800
    n = 0
    for r in rows:
        conn.execute(
            """INSERT OR REPLACE INTO market_regime_hmm_daily
               (trade_date, index_code, hmm_state, regime_bucket, model_version, updated_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))""",
            (r["trade_date"], idx, r.get("hmm_state"), r.get("regime_bucket"), model_version),
        )
        n += 1
    conn.commit()
    return n


def hmm_rows_as_regime_rows(hmm_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"trade_date": r["trade_date"], "bucket": r["regime_bucket"]}
        for r in hmm_rows
    ]


def compare_hmm_vs_rules(
    conn: sqlite3.Connection,
    *,
    days: int = 730,
    train_ratio: float = 0.85,
) -> dict[str, Any]:
    """HMM vs 规则 L1（confirmed bucket）对照报告。"""
    dates, X = load_hmm_features(conn, days=days)
    if len(dates) < 80:
        return {"error": "特征样本不足（需 market_regime_daily ≥80 天）"}

    split = int(len(dates) * train_ratio)
    if split < 60:
        split = len(dates) - 30
    train_dates, test_dates = dates[:split], dates[split:]
    X_train, X_test = X[:split], X[split:]

    fit = fit_hmm(X_train)
    hmm_all = predict_buckets(fit, dates, X)
    hmm_test = [r for r in hmm_all if r["trade_date"] in set(test_dates)]

    rule_rows = load_regime_rows(conn, primary="csi800", days=days)
    rule_by_date = {r["trade_date"]: r.get("bucket") for r in rule_rows}

    from services.market_index import fetch_index_kline

    kline = fetch_index_kline(
        config.REGIME_INDEX_CSI800, period="daily", days=min(days + 120, 800), with_technical=False,
    )
    index_returns = index_returns_from_kline(kline.get("kline") or [])

    # 全样本 HMM regime rows（对照用）
    hmm_regime_rows = hmm_rows_as_regime_rows(hmm_all)
    rule_test_rows = [r for r in rule_rows if r["trade_date"] in set(test_dates)]
    hmm_test_rows = hmm_rows_as_regime_rows(hmm_test)

    # OOS 一致率
    agree = 0
    total = 0
    for r in hmm_test:
        td = r["trade_date"]
        rb = rule_by_date.get(td)
        if rb:
            total += 1
            if rb == r["regime_bucket"]:
                agree += 1

    hmm_dist: dict[str, int] = {b: 0 for b in REGIME_BUCKET_ORDER}
    rule_dist: dict[str, int] = {b: 0 for b in REGIME_BUCKET_ORDER}
    for r in hmm_all:
        b = r.get("regime_bucket")
        if b in hmm_dist:
            hmm_dist[b] += 1
    for r in rule_rows:
        b = r.get("bucket")
        if b in rule_dist:
            rule_dist[b] += 1

    hmm_buckets = [r["bucket"] for r in hmm_regime_rows]
    rule_buckets = [r["bucket"] for r in rule_rows if r.get("bucket") in REGIME_BUCKET_ORDER]

    hmm_dwell = compute_dwell_times(hmm_buckets)
    rule_dwell = compute_dwell_times(rule_buckets)
    hmm_internal = internal_consistency_report(hmm_regime_rows, index_returns)
    rule_internal = internal_consistency_report(rule_rows, index_returns)

    return {
        "generated_at": date.today().isoformat(),
        "model_version": HMM_MODEL_VERSION,
        "sample_days": len(dates),
        "train_days": split,
        "test_days": len(test_dates),
        "start_date": dates[0],
        "end_date": dates[-1],
        "state_mapping": fit.state_stats,
        "state_to_bucket": fit.state_to_bucket,
        "distribution_hmm": hmm_dist,
        "distribution_rules": rule_dist,
        "oos_bucket_agreement_pct": round(agree / total * 100, 1) if total else None,
        "oos_samples": total,
        "dwell_time_hmm": hmm_dwell,
        "dwell_time_rules": rule_dwell,
        "internal_hmm": {
            "return_anova_p": hmm_internal.get("return_anova", {}).get("p_value"),
            "significant": hmm_internal.get("return_anova", {}).get("significant_05"),
            "verdict": hmm_internal.get("verdict"),
        },
        "internal_rules": {
            "return_anova_p": rule_internal.get("return_anova", {}).get("p_value"),
            "significant": rule_internal.get("return_anova", {}).get("significant_05"),
            "verdict": rule_internal.get("verdict"),
        },
        "verdict": _compare_verdict(
            agree, total, hmm_dwell, rule_dwell,
            hmm_internal, rule_internal,
        ),
        "note": "HMM 为对照层；生产 L1/L2/L3 仍用规则 + persistence",
    }


def _compare_verdict(
    agree: int,
    total: int,
    hmm_dwell: dict,
    rule_dwell: dict,
    hmm_internal: dict,
    rule_internal: dict,
) -> str:
    parts = []
    if total:
        pct = agree / total * 100
        parts.append(f"OOS 桶一致率 {pct:.1f}%（{agree}/{total}）")
    hm = hmm_dwell.get("overall_mean_days") or 0
    rm = rule_dwell.get("overall_mean_days") or 0
    parts.append(f"HMM 平均停留 {hm} 天 vs 规则 {rm} 天")
    h_sig = hmm_internal.get("return_anova", {}).get("significant_05")
    r_sig = rule_internal.get("return_anova", {}).get("significant_05")
    if h_sig and not r_sig:
        parts.append("HMM 收益区分度优于规则")
    elif r_sig and not h_sig:
        parts.append("规则收益区分度优于 HMM")
    elif h_sig and r_sig:
        parts.append("两者收益区分度均显著")
    else:
        parts.append("两者收益区分度均不显著")
    return "；".join(parts)


def fit_and_persist_full_sample(
    conn: sqlite3.Connection,
    *,
    days: int = 730,
) -> dict[str, Any]:
    dates, X = load_hmm_features(conn, days=days)
    if len(dates) < 60:
        return {"error": "样本不足", "persisted": 0}
    fit = fit_hmm(X)
    rows = predict_buckets(fit, dates, X)
    n = persist_hmm_buckets(conn, rows)
    return {
        "persisted": n,
        "model_version": HMM_MODEL_VERSION,
        "state_to_bucket": fit.state_to_bucket,
        "state_stats": fit.state_stats,
        "distribution": {b: sum(1 for r in rows if r["regime_bucket"] == b) for b in REGIME_BUCKET_ORDER},
    }


def format_compare_report_text(report: dict[str, Any]) -> str:
    if report.get("error"):
        return f"错误: {report['error']}"
    lines = [
        "📊 HMM vs 规则 L1 对照",
        "━" * 48,
        f"样本: {report.get('start_date')} → {report.get('end_date')} ({report.get('sample_days')} 天)",
        f"训练/测试: {report.get('train_days')} / {report.get('test_days')} 天",
        f"OOS 桶一致率: {report.get('oos_bucket_agreement_pct')}% (n={report.get('oos_samples')})",
        "",
        "状态 → 四格映射:",
    ]
    for st in report.get("state_mapping") or []:
        lines.append(
            f"  state {st['state']} → {st.get('bucket')} "
            f"(n={st.get('count')}, ret20={st.get('ret20', 0):+.3f}, vol={st.get('vol20', 0):.3f})"
        )
    lines.extend([
        "",
        f"HMM 分布:   {report.get('distribution_hmm')}",
        f"规则分布:   {report.get('distribution_rules')}",
        "",
        f"HMM  停留: {report.get('dwell_time_hmm', {}).get('overall_mean_days')} 天 · "
        f"切换 {report.get('dwell_time_hmm', {}).get('total_transitions')} 次",
        f"规则 停留: {report.get('dwell_time_rules', {}).get('overall_mean_days')} 天 · "
        f"切换 {report.get('dwell_time_rules', {}).get('total_transitions')} 次",
        "",
        f"HMM  内部: {report.get('internal_hmm', {}).get('verdict', '')[:70]}",
        f"规则 内部: {report.get('internal_rules', {}).get('verdict', '')[:70]}",
        "",
        report.get("verdict", ""),
        "",
        report.get("note", ""),
    ])
    return "\n".join(lines)
