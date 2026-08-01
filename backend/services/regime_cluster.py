"""P3-D：K-Means / GMM 四格 regime — sklearn 实现，与规则 L1 并行对照。"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

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

ClusterMethod = Literal["kmeans", "gmm"]
CLUSTER_MODEL_VERSION = "sklearn_cluster_v1"
N_CLUSTERS = 4


@dataclass
class ClusterFitResult:
    method: ClusterMethod
    model: Any
    cluster_to_bucket: dict[int, str]
    cluster_stats: list[dict[str, Any]]
    feature_means: list[float]
    feature_stds: list[float]


def _standardize_fit(X: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd = np.where(sd < 1e-9, 1.0, sd)
    return (X - mu) / sd, mu, sd


def _standardize_apply(X: np.ndarray, mu: np.ndarray, sd: np.ndarray) -> np.ndarray:
    return (X - mu) / sd


def _cluster_feature_stats(X: np.ndarray, labels: np.ndarray) -> list[dict[str, Any]]:
    out = []
    for c in range(N_CLUSTERS):
        mask = labels == c
        if not mask.any():
            out.append({
                "cluster": c, "count": 0,
                "ret20": 0.0, "vol20": 0.0, "adx": 0.0, "pv_ma60": 0.0,
            })
            continue
        chunk = X[mask]
        out.append({
            "cluster": c,
            "count": int(mask.sum()),
            "ret20": float(chunk[:, 0].mean()),
            "vol20": float(chunk[:, 1].mean()),
            "adx": float(chunk[:, 2].mean()),
            "pv_ma60": float(chunk[:, 4].mean()),
        })
    return out


def _map_clusters_to_buckets(cluster_stats: list[dict[str, Any]]) -> dict[int, str]:
    """复用 HMM 质心语义映射（state → cluster 字段名兼容）。"""
    adapted = [{"state": s["cluster"], **s} for s in cluster_stats]
    return map_states_to_buckets(adapted)


def _transition_matrix(buckets: list[str]) -> dict[str, dict[str, float]]:
    """簇/桶转移频率（行=from，列=to，概率）。"""
    order = REGIME_BUCKET_ORDER
    counts: dict[str, dict[str, int]] = {b: {t: 0 for t in order} for b in order}
    total: dict[str, int] = {b: 0 for b in order}
    for i in range(1, len(buckets)):
        a, b = buckets[i - 1], buckets[i]
        if a not in counts or b not in counts:
            continue
        counts[a][b] += 1
        total[a] += 1
    out: dict[str, dict[str, float]] = {}
    for a in order:
        denom = total[a] or 1
        out[a] = {t: round(counts[a][t] / denom, 3) for t in order}
    return out


def fit_cluster(
    X: np.ndarray,
    *,
    method: ClusterMethod = "gmm",
    n_clusters: int = N_CLUSTERS,
    random_state: int = 42,
) -> ClusterFitResult:
    """在训练集上拟合 K-Means 或 GMM（特征已标准化）。"""
    Xs, mu, sd = _standardize_fit(X)

    if method == "kmeans":
        from sklearn.cluster import KMeans

        model = KMeans(n_clusters=n_clusters, n_init=10, random_state=random_state)
        model.fit(Xs)
        labels = model.predict(Xs)
    else:
        from sklearn.mixture import GaussianMixture

        model = GaussianMixture(
            n_components=n_clusters,
            covariance_type="diag",
            n_init=5,
            random_state=random_state,
        )
        model.fit(Xs)
        labels = model.predict(Xs)

    raw_stats = _cluster_feature_stats(X, labels)
    cluster_to_bucket = _map_clusters_to_buckets(raw_stats)
    enriched = [
        {**st, "bucket": cluster_to_bucket.get(st["cluster"], "oscillation")}
        for st in raw_stats
    ]

    return ClusterFitResult(
        method=method,
        model=model,
        cluster_to_bucket=cluster_to_bucket,
        cluster_stats=enriched,
        feature_means=mu.tolist(),
        feature_stds=sd.tolist(),
    )


def predict_cluster_buckets(
    fit: ClusterFitResult,
    dates: list[str],
    X: np.ndarray,
) -> list[dict[str, Any]]:
    mu = np.asarray(fit.feature_means)
    sd = np.asarray(fit.feature_stds)
    Xs = _standardize_apply(X, mu, sd)
    labels = fit.model.predict(Xs)
    rows = []
    for i, td in enumerate(dates):
        cid = int(labels[i])
        bucket = fit.cluster_to_bucket.get(cid, "oscillation")
        rows.append({
            "trade_date": td,
            "cluster_id": cid,
            "regime_bucket": bucket,
            "regime_bucket_label": regime_bucket_label(bucket),
            "method": fit.method,
        })
    return rows


def cluster_rows_as_regime_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"trade_date": r["trade_date"], "bucket": r["regime_bucket"]} for r in rows]


def _method_report(
    *,
    method: ClusterMethod,
    dates: list[str],
    X: np.ndarray,
    split: int,
    test_dates: list[str],
    rule_rows: list[dict[str, Any]],
    rule_by_date: dict[str, str | None],
    index_returns: dict[str, float],
) -> dict[str, Any]:
    X_train = X[:split]
    fit = fit_cluster(X_train, method=method)
    all_rows = predict_cluster_buckets(fit, dates, X)
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

    regime_rows = cluster_rows_as_regime_rows(all_rows)
    buckets = [r["bucket"] for r in regime_rows if r.get("bucket") in REGIME_BUCKET_ORDER]
    rule_buckets = [r["bucket"] for r in rule_rows if r.get("bucket") in REGIME_BUCKET_ORDER]

    dwell = compute_dwell_times(buckets)
    rule_dwell = compute_dwell_times(rule_buckets)
    internal = internal_consistency_report(regime_rows, index_returns)
    rule_internal = internal_consistency_report(rule_rows, index_returns)

    bic = None
    if method == "gmm" and hasattr(fit.model, "bic"):
        mu = np.asarray(fit.feature_means)
        sd = np.asarray(fit.feature_stds)
        Xs = _standardize_apply(X[:split], mu, sd)
        try:
            bic = float(fit.model.bic(Xs))
        except Exception:
            bic = None

    return {
        "method": method,
        "model_version": CLUSTER_MODEL_VERSION,
        "cluster_mapping": fit.cluster_stats,
        "cluster_to_bucket": fit.cluster_to_bucket,
        "distribution": dist,
        "oos_bucket_agreement_pct": round(agree / total * 100, 1) if total else None,
        "oos_samples": total,
        "dwell_time": dwell,
        "transition_matrix": _transition_matrix(buckets),
        "internal": {
            "return_anova_p": internal.get("return_anova", {}).get("p_value"),
            "significant": internal.get("return_anova", {}).get("significant_05"),
            "verdict": internal.get("verdict"),
        },
        "bic": bic,
        "verdict": _compare_verdict(
            agree, total, dwell, rule_dwell, internal, rule_internal,
        ).replace("HMM", method.upper()),
    }


def compare_cluster_vs_rules(
    conn: sqlite3.Connection,
    *,
    days: int = 730,
    train_ratio: float = 0.85,
    methods: tuple[ClusterMethod, ...] = ("kmeans", "gmm"),
) -> dict[str, Any]:
    """K-Means / GMM vs 规则 L1（confirmed bucket）对照；训练集拟合 + 映射，防泄漏。"""
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

    rule_buckets = [r["bucket"] for r in rule_rows if r.get("bucket") in REGIME_BUCKET_ORDER]
    rule_dwell = compute_dwell_times(rule_buckets)

    method_reports: dict[str, Any] = {}
    for m in methods:
        method_reports[m] = _method_report(
            method=m,
            dates=dates,
            X=X,
            split=split,
            test_dates=test_dates,
            rule_rows=rule_rows,
            rule_by_date=rule_by_date,
            index_returns=index_returns,
        )

    cross_pct = None
    if "kmeans" in method_reports and "gmm" in method_reports:
        km = {
            r["trade_date"]: r["regime_bucket"]
            for r in predict_cluster_buckets(
                fit_cluster(X[:split], method="kmeans"), dates, X,
            )
        }
        gm = {
            r["trade_date"]: r["regime_bucket"]
            for r in predict_cluster_buckets(
                fit_cluster(X[:split], method="gmm"), dates, X,
            )
        }
        agree = sum(1 for d in dates if km.get(d) and km.get(d) == gm.get(d))
        cross_pct = round(agree / len(dates) * 100, 1) if dates else None

    return {
        "generated_at": date.today().isoformat(),
        "feature_names": list(FEATURE_NAMES),
        "sample_days": len(dates),
        "train_days": split,
        "test_days": len(test_dates),
        "start_date": dates[0],
        "end_date": dates[-1],
        "distribution_rules": rule_dist,
        "dwell_time_rules": rule_dwell,
        "methods": method_reports,
        "cross_method_agreement_pct": cross_pct,
        "note": "sklearn 聚类为对照层；生产 L1/L2/L3 仍用规则 + persistence",
    }


def persist_cluster_buckets(
    conn: sqlite3.Connection,
    rows: list[dict[str, Any]],
    *,
    index_code: str | None = None,
    model_version: str = CLUSTER_MODEL_VERSION,
) -> int:
    idx = index_code or config.REGIME_INDEX_CSI800
    n = 0
    for r in rows:
        conn.execute(
            """INSERT OR REPLACE INTO market_regime_cluster_daily
               (trade_date, index_code, method, cluster_id, regime_bucket,
                model_version, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
            (
                r["trade_date"],
                idx,
                r.get("method"),
                r.get("cluster_id"),
                r.get("regime_bucket"),
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
    methods: tuple[ClusterMethod, ...] = ("kmeans", "gmm"),
) -> dict[str, Any]:
    dates, X = load_hmm_features(conn, days=days)
    if len(dates) < 60:
        return {"error": "样本不足", "persisted": 0}

    out: dict[str, Any] = {"persisted": 0, "methods": {}}
    total = 0
    for m in methods:
        fit = fit_cluster(X, method=m)
        rows = predict_cluster_buckets(fit, dates, X)
        total += persist_cluster_buckets(conn, rows)
        out["methods"][m] = {
            "cluster_to_bucket": fit.cluster_to_bucket,
            "cluster_stats": fit.cluster_stats,
            "distribution": {
                b: sum(1 for r in rows if r["regime_bucket"] == b) for b in REGIME_BUCKET_ORDER
            },
        }
    out["persisted"] = total
    out["model_version"] = CLUSTER_MODEL_VERSION
    return out


def format_compare_report_text(report: dict[str, Any]) -> str:
    if report.get("error"):
        return f"错误: {report['error']}"

    lines = [
        "📊 K-Means / GMM vs 规则 L1 对照",
        "━" * 48,
        f"特征: {', '.join(report.get('feature_names') or FEATURE_NAMES)}",
        f"样本: {report.get('start_date')} → {report.get('end_date')} ({report.get('sample_days')} 天)",
        f"训练/测试: {report.get('train_days')} / {report.get('test_days')} 天",
        f"规则分布: {report.get('distribution_rules')}",
        f"规则停留: {report.get('dwell_time_rules', {}).get('overall_mean_days')} 天",
        "",
    ]
    if report.get("cross_method_agreement_pct") is not None:
        lines.append(f"K-Means ↔ GMM 桶一致率: {report['cross_method_agreement_pct']}%")
        lines.append("")

    for method, mr in (report.get("methods") or {}).items():
        lines.extend([
            f"── {method.upper()} ──",
            f"OOS 桶一致率: {mr.get('oos_bucket_agreement_pct')}% (n={mr.get('oos_samples')})",
            "簇 → 四格:",
        ])
        for st in mr.get("cluster_mapping") or []:
            lines.append(
                f"  cluster {st['cluster']} → {st.get('bucket')} "
                f"(n={st.get('count')}, ret20={st.get('ret20', 0):+.3f}, vol={st.get('vol20', 0):.3f})"
            )
        if mr.get("bic") is not None:
            lines.append(f"BIC (train): {mr['bic']:.1f}")
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
