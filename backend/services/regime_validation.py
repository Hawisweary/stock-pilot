"""市场状态划分验证 — 内部一致性 / Walk-Forward / 策略条件有效性。

严格因果：历史标签与 walk-forward 预测均只使用截止当日（walk-forward 用 t-1）的数据。
"""
from __future__ import annotations

import math
import random
import sqlite3
from datetime import date, timedelta
from typing import Any, Optional

import config
from services.market_index import fetch_index_kline
from services.market_regime import (
    REGIME_BUCKET_ORDER,
    REGIME_BUCKET_STRATEGY_MAP,
    classify_regime,
    compute_market_features,
    regime_bucket,
    regime_bucket_label,
)

REGIME_BUCKET_ORDER = REGIME_BUCKET_ORDER  # re-export

# 四格 → 推荐策略（L3；须为 backtest_engine 已支持策略）
REGIME_RECOMMENDED_STRATEGY = REGIME_BUCKET_STRATEGY_MAP

# 七格 → 推荐策略（细粒度归因 / 与 V5 对齐，L3 备选）
REGIME_RECOMMENDED_STRATEGY_7: dict[str, str] = {
    "strong_trend_up": "momentum",
    "weak_trend_up": "momentum",
    "high_volatility": "turtle",
    "oscillation": "composite",
    "strong_trend_down": "dividend_defensive",
    "weak_trend_down": "dividend_defensive",
    "liquidity_drought": "dividend_defensive",
}

BACKTEST_READY_STRATEGIES = frozenset({
    "momentum", "turtle", "composite", "index_enhance", "sector_rotation",
    "dividend_defensive", "dual_ma",
})

MIN_IN_REGIME_DAYS = 15

MIN_BUCKET_SAMPLES = 20


def _bucket_col(primary: str) -> tuple[str, str]:
    """返回 (db_column, index_code)。"""
    if primary == "csi300":
        return "regime_bucket_csi300", config.REGIME_INDEX_CSI300
    return "regime_bucket_csi800", config.REGIME_INDEX_CSI800


def index_returns_from_kline(kline: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    prev_close: float | None = None
    for bar in kline:
        d = str(bar.get("date") or "")
        close = bar.get("close")
        if not d or close is None:
            continue
        c = float(close)
        if prev_close and prev_close > 0:
            out[d] = c / prev_close - 1.0
        prev_close = c
    return out


def load_regime_rows(
    conn: sqlite3.Connection,
    *,
    primary: str = "csi800",
    days: int = 730,
) -> list[dict[str, Any]]:
    bucket_col, _ = _bucket_col(primary)
    cols = conn.execute("PRAGMA table_info(market_regime_daily)").fetchall()
    col_names = {r[1] for r in cols}
    if bucket_col not in col_names:
        return []

    rows = conn.execute(
        f"""SELECT trade_date, regime, regime_label, {bucket_col} AS bucket,
                   regime_csi800, regime_csi800_label,
                   volatility_20, price_vs_ma60, price_vs_ma60_csi800,
                   regime_label_agreement
            FROM market_regime_daily
            ORDER BY trade_date DESC LIMIT ?""",
        (days,),
    ).fetchall()
    out = []
    for r in reversed(rows):
        bucket = r[3]
        regime_csi800 = r[4]
        pv60_csi800 = float(r[8] if r[8] is not None else r[7] or 0)
        pv60_csi300 = float(r[7] or 0)
        regime_for_bucket = regime_csi800 if primary == "csi800" and regime_csi800 else r[1]
        pv60 = pv60_csi800 if primary == "csi800" else pv60_csi300
        if not bucket and regime_for_bucket:
            bucket = regime_bucket(str(regime_for_bucket), pv60)
        if not bucket:
            continue
        out.append({
            "trade_date": r[0],
            "regime": r[1],
            "regime_label": r[2],
            "regime_csi800": regime_csi800 or r[1],
            "regime_csi800_label": r[5],
            "bucket": bucket,
            "volatility_20": r[6],
            "price_vs_ma60": pv60,
            "regime_label_agreement": r[9],
        })
    return out


def load_jump_regime_rows(
    conn: sqlite3.Connection,
    *,
    days: int = 730,
) -> list[dict[str, Any]]:
    """从 market_regime_jump_daily 加载四格标签（与 load_regime_rows 同结构）。"""
    if not conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='market_regime_jump_daily'",
    ).fetchone():
        return []

    rows = conn.execute(
        """SELECT trade_date, regime_bucket AS bucket, jump_penalty, backend, model_version
           FROM market_regime_jump_daily
           WHERE regime_bucket IS NOT NULL AND regime_bucket != ''
           ORDER BY trade_date DESC LIMIT ?""",
        (days,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in reversed(rows):
        bucket = r[1]
        if bucket not in REGIME_BUCKET_ORDER:
            continue
        out.append({
            "trade_date": r[0],
            "bucket": bucket,
            "jump_penalty": r[2],
            "backend": r[3],
            "model_version": r[4],
        })
    return out


def build_causal_regime_series(
    kline: list[dict[str, Any]],
    *,
    min_bars: int = 65,
    use_features: bool = False,
    conn: Optional[sqlite3.Connection] = None,
) -> list[dict[str, Any]]:
    """逐日因果分类：第 t 日标签仅使用截至 t 的 K 线（与 sync_regime 一致）。"""
    series: list[dict[str, Any]] = []
    for i in range(min_bars - 1, len(kline)):
        sub = kline[: i + 1]
        td = str(sub[-1].get("date") or "")
        features = None
        if use_features and conn and td:
            try:
                features = compute_market_features(conn, td)
            except Exception:
                features = None
        r = classify_regime(sub, features=features)
        b = regime_bucket(r["regime"], float(r.get("price_vs_ma60") or 0))
        series.append({
            "trade_date": td,
            "regime": r["regime"],
            "regime_label": r.get("regime_label"),
            "bucket": b,
            "volatility_20": r.get("volatility_20"),
            "price_vs_ma60": r.get("price_vs_ma60"),
        })
    return series


def _group_values(values_by_bucket: dict[str, list[float]]) -> list[list[float]]:
    return [values_by_bucket[b] for b in REGIME_BUCKET_ORDER if values_by_bucket.get(b)]


def _f_statistic(groups: list[list[float]]) -> float:
    if len(groups) < 2:
        return 0.0
    all_vals = [x for g in groups for x in g]
    if len(all_vals) < 3:
        return 0.0
    grand_mean = sum(all_vals) / len(all_vals)
    ss_between = 0.0
    ss_within = 0.0
    n_total = len(all_vals)
    for g in groups:
        if not g:
            continue
        mg = sum(g) / len(g)
        ss_between += len(g) * (mg - grand_mean) ** 2
        ss_within += sum((x - mg) ** 2 for x in g)
    df_between = len(groups) - 1
    df_within = n_total - len(groups)
    if df_within <= 0 or ss_within <= 0:
        return 0.0
    ms_between = ss_between / df_between
    ms_within = ss_within / df_within
    return ms_between / ms_within if ms_within > 0 else 0.0


def permutation_anova_pvalue(groups: list[list[float]], *, n_perm: int = 1999, seed: int = 42) -> float:
    """无 scipy 依赖的置换检验 p 值。"""
    flat = [(v, i) for i, g in enumerate(groups) for v in g]
    if len(flat) < 10 or len(groups) < 2:
        return 1.0
    f_obs = _f_statistic(groups)
    rng = random.Random(seed)
    count = 0
    n_groups = len(groups)
    sizes = [len(g) for g in groups]
    for _ in range(n_perm):
        rng.shuffle(flat)
        perm_groups: list[list[float]] = [[] for _ in range(n_groups)]
        idx = 0
        for gi, sz in enumerate(sizes):
            perm_groups[gi] = [flat[idx + j][0] for j in range(sz)]
            idx += sz
        if _f_statistic(perm_groups) >= f_obs:
            count += 1
    return (count + 1) / (n_perm + 1)


def compute_dwell_times(bucket_series: list[str]) -> dict[str, Any]:
    if not bucket_series:
        return {"overall_mean_days": 0, "by_bucket": {}}
    runs: list[tuple[str, int]] = []
    cur = bucket_series[0]
    length = 1
    for b in bucket_series[1:]:
        if b == cur:
            length += 1
        else:
            runs.append((cur, length))
            cur = b
            length = 1
    runs.append((cur, length))

    by_bucket: dict[str, list[int]] = {b: [] for b in REGIME_BUCKET_ORDER}
    for b, ln in runs:
        by_bucket.setdefault(b, []).append(ln)

    summary = {}
    for b in REGIME_BUCKET_ORDER:
        arr = by_bucket.get(b) or []
        summary[b] = {
            "label": regime_bucket_label(b),
            "run_count": len(arr),
            "mean_dwell_days": round(sum(arr) / len(arr), 1) if arr else 0,
            "max_dwell_days": max(arr) if arr else 0,
        }
    overall = [ln for _, ln in runs]
    return {
        "overall_mean_days": round(sum(overall) / len(overall), 1) if overall else 0,
        "total_transitions": max(len(runs) - 1, 0),
        "by_bucket": summary,
    }


def internal_consistency_report(
    regime_rows: list[dict[str, Any]],
    index_returns: dict[str, float],
) -> dict[str, Any]:
    """第一层：组内/组间统计差异 + 停留时间。"""
    by_bucket: dict[str, list[float]] = {b: [] for b in REGIME_BUCKET_ORDER}
    bucket_counts: dict[str, int] = {b: 0 for b in REGIME_BUCKET_ORDER}
    vol_by_bucket: dict[str, list[float]] = {b: [] for b in REGIME_BUCKET_ORDER}

    ordered_buckets: list[str] = []
    for row in regime_rows:
        b = row.get("bucket")
        td = row.get("trade_date")
        if b not in REGIME_BUCKET_ORDER or not td:
            continue
        ordered_buckets.append(b)
        bucket_counts[b] += 1
        ret = index_returns.get(td)
        if ret is not None:
            by_bucket[b].append(ret)
        v = row.get("volatility_20")
        if v is not None:
            vol_by_bucket[b].append(float(v))

    groups = _group_values(by_bucket)
    f_ret = _f_statistic(groups)
    p_ret = permutation_anova_pvalue(groups) if len(groups) >= 2 else 1.0

    vol_groups = _group_values(vol_by_bucket)
    f_vol = _f_statistic(vol_groups)
    p_vol = permutation_anova_pvalue(vol_groups) if len(vol_groups) >= 2 else 1.0

    bucket_stats = []
    for b in REGIME_BUCKET_ORDER:
        rets = by_bucket.get(b) or []
        n = bucket_counts.get(b) or 0
        mean_ret = sum(rets) / len(rets) if rets else None
        vol = 0.0
        if len(rets) > 1:
            m = mean_ret or 0
            vol = math.sqrt(sum((x - m) ** 2 for x in rets) / (len(rets) - 1))
        bucket_stats.append({
            "bucket": b,
            "label": regime_bucket_label(b),
            "days": n,
            "sample_sufficient": n >= MIN_BUCKET_SAMPLES,
            "mean_daily_return_pct": round(mean_ret * 100, 4) if mean_ret is not None else None,
            "daily_vol_pct": round(vol * 100, 4) if rets else None,
            "return_observations": len(rets),
        })

    low_sample = [s["label"] for s in bucket_stats if not s["sample_sufficient"]]
    dwell = compute_dwell_times(ordered_buckets)

    return {
        "sample_days": len(regime_rows),
        "bucket_stats": bucket_stats,
        "low_sample_buckets": low_sample,
        "return_anova": {
            "f_statistic": round(f_ret, 4),
            "p_value": round(p_ret, 4),
            "significant_05": p_ret < 0.05,
        },
        "volatility_anova": {
            "f_statistic": round(f_vol, 4),
            "p_value": round(p_vol, 4),
            "significant_05": p_vol < 0.05,
        },
        "dwell_time": dwell,
        "verdict": _internal_verdict(p_ret, p_vol, low_sample, dwell),
    }


def _internal_verdict(p_ret: float, p_vol: float, low_sample: list, dwell: dict) -> str:
    parts = []
    if p_ret < 0.05:
        parts.append("不同状态下指数日收益差异显著")
    else:
        parts.append("日收益组间差异未达显著（p≥0.05）")
    if p_vol < 0.05:
        parts.append("波动率组间差异显著")
    if low_sample:
        parts.append(f"样本不足：<{MIN_BUCKET_SAMPLES}天 — {', '.join(low_sample)}")
    if dwell.get("overall_mean_days", 0) >= 5:
        parts.append(f"平均停留 {dwell['overall_mean_days']} 天，状态具粘性")
    else:
        parts.append("状态切换频繁，或样本较短")
    return "；".join(parts)


def walk_forward_report(
    kline: list[dict[str, Any]],
    *,
    min_bars: int = 65,
    conn: Optional[sqlite3.Connection] = None,
) -> dict[str, Any]:
    """第二层：t-1 因果预测 vs t 日实际；持久性基线；切换滞后。"""
    if len(kline) < min_bars + 1:
        return {"error": "K 线不足", "sample_days": 0}

    lag_matches = 0
    persist_matches = 0
    bucket_matches = 0
    transition_lags: list[int] = []
    n = 0

    prev_actual_bucket: str | None = None
    pending_transition_day: int | None = None
    causal_at_transition: str | None = None

    for i in range(min_bars, len(kline)):
        causal = classify_regime(kline[:i], features=None)
        actual = classify_regime(kline[: i + 1], features=None)
        pred_b = regime_bucket(causal["regime"], float(causal.get("price_vs_ma60") or 0))
        act_b = regime_bucket(actual["regime"], float(actual.get("price_vs_ma60") or 0))
        n += 1
        if pred_b == act_b:
            bucket_matches += 1
        if causal["regime"] == actual["regime"]:
            lag_matches += 1
        if prev_actual_bucket is not None and pred_b == prev_actual_bucket:
            persist_matches += 1

        if prev_actual_bucket is not None and act_b != prev_actual_bucket:
            if causal_at_transition is None or causal_at_transition != prev_actual_bucket:
                transition_lags.append(0)
            else:
                transition_lags.append(max(i - (pending_transition_day or i), 0))
            pending_transition_day = i
            causal_at_transition = pred_b
        prev_actual_bucket = act_b

    # 5 日窗口：t-1 桶是否等于 t..t+4 多数桶
    series = build_causal_regime_series(kline, min_bars=min_bars, use_features=False)
    buckets = [r["bucket"] for r in series]
    horizon5_matches = 0
    horizon5_n = 0
    for i in range(len(buckets) - 5):
        pred = buckets[i]
        window = buckets[i + 1 : i + 6]
        majority = max(set(window), key=window.count)
        horizon5_n += 1
        if pred == majority:
            horizon5_matches += 1

    return {
        "sample_days": n,
        "method": "index_only_causal_t_minus_1",
        "note": "严格因果：预测仅用 t-1 及之前 K 线；实际为含 t 日收盘。未含全市场广度特征。",
        "regime_match_rate_pct": round(lag_matches / n * 100, 1) if n else None,
        "bucket_match_rate_pct": round(bucket_matches / n * 100, 1) if n else None,
        "persistence_baseline_pct": round(persist_matches / max(n - 1, 1) * 100, 1) if n > 1 else None,
        "horizon5_majority_match_pct": round(horizon5_matches / horizon5_n * 100, 1) if horizon5_n else None,
        "transition_detection": {
            "samples": len(transition_lags),
            "mean_lag_days": round(sum(transition_lags) / len(transition_lags), 1) if transition_lags else None,
        },
        "verdict": _walk_forward_verdict(n, bucket_matches, persist_matches, horizon5_n, horizon5_matches),
    }


def _walk_forward_verdict(n, bucket_m, persist_m, h5n, h5m) -> str:
    if n < 60:
        return "样本偏短，建议回填 ≥252 交易日后再评估"
    b_pct = bucket_m / n * 100 if n else 0
    p_pct = persist_m / max(n - 1, 1) * 100 if n > 1 else 0
    h5_pct = h5m / h5n * 100 if h5n else 0
    parts = [f"t-1 桶匹配率 {b_pct:.1f}%（随机四格约 25%）"]
    if b_pct >= 70:
        parts.append("预测能力较好")
    elif b_pct >= 60:
        parts.append("预测能力尚可")
    else:
        parts.append("预测能力一般，状态切换较频繁")
    parts.append(f"持久性基线 {p_pct:.1f}%")
    if h5n:
        parts.append(f"5日多数匹配 {h5_pct:.1f}%")
    return "；".join(parts)


def forward_return_report(
    regime_rows: list[dict[str, Any]],
    index_returns: dict[str, float],
    *,
    horizons: tuple[int, ...] = (1, 5),
) -> dict[str, Any]:
    """前瞻收益：状态标签对未来收益的区分度（无策略，仅指数）。"""
    dates = [r["trade_date"] for r in regime_rows]
    date_idx = {d: i for i, d in enumerate(dates)}
    out_horizons = {}

    for h in horizons:
        by_bucket: dict[str, list[float]] = {b: [] for b in REGIME_BUCKET_ORDER}
        for row in regime_rows:
            b = row.get("bucket")
            td = row.get("trade_date")
            if b not in REGIME_BUCKET_ORDER or td not in date_idx:
                continue
            i = date_idx[td]
            if i + h >= len(dates):
                continue
            start_d, end_d = dates[i], dates[i + h]
            r_start = index_returns.get(start_d)
            if r_start is None:
                continue
            compound = 1.0
            ok = True
            for j in range(1, h + 1):
                ri = index_returns.get(dates[i + j])
                if ri is None:
                    ok = False
                    break
                compound *= 1 + ri
            if ok:
                by_bucket[b].append(compound - 1.0)

        stats = []
        for b in REGIME_BUCKET_ORDER:
            arr = by_bucket.get(b) or []
            stats.append({
                "bucket": b,
                "label": regime_bucket_label(b),
                "mean_forward_return_pct": round(sum(arr) / len(arr) * 100, 2) if arr else None,
                "observations": len(arr),
            })
        groups = _group_values(by_bucket)
        p = permutation_anova_pvalue(groups) if len(groups) >= 2 else 1.0
        out_horizons[f"h{h}"] = {
            "horizon_days": h,
            "bucket_stats": stats,
            "anova_p_value": round(p, 4),
            "significant_05": p < 0.05,
        }

    return {"forward_returns": out_horizons}


def strategy_conditional_report(
    conn: sqlite3.Connection,
    regime_rows: list[dict[str, Any]],
    *,
    days: int = 180,
    strategies: Optional[list[str]] = None,
    use_seven_state: bool = False,
    primary: str = "csi800",
) -> dict[str, Any]:
    """第三层：推荐策略在「匹配状态」vs「非匹配状态」下的日收益对比。"""
    from services.backtest_engine import run_backtest

    if use_seven_state and primary == "csi800":
        state_by_date = {
            r["trade_date"]: r.get("regime_csi800") or r.get("regime")
            for r in regime_rows
            if r.get("trade_date")
        }
        mapping = REGIME_RECOMMENDED_STRATEGY_7
        match_key = "regime_7"
    else:
        state_by_date = {r["trade_date"]: r["bucket"] for r in regime_rows if r.get("bucket")}
        mapping = REGIME_RECOMMENDED_STRATEGY
        match_key = "bucket"

    if not state_by_date:
        return {"error": "无 regime 标签", "results": []}

    sorted_dates = sorted(state_by_date.keys())
    lagged_state: dict[str, str] = {}
    for i, d in enumerate(sorted_dates):
        if i > 0:
            lagged_state[d] = state_by_date[sorted_dates[i - 1]]

    strats = strategies or sorted(set(mapping.values()) & BACKTEST_READY_STRATEGIES)
    results = []

    for strategy in strats:
        if strategy not in BACKTEST_READY_STRATEGIES:
            results.append({"strategy": strategy, "error": "回测引擎未支持"})
            continue
        bt = run_backtest(days=days, strategy=strategy, rebalance="weekly")
        if bt.get("error"):
            results.append({"strategy": strategy, "error": bt["error"]})
            continue
        daily = bt.get("daily_values") or []
        in_recommended: list[float] = []
        out_recommended: list[float] = []
        recommended_states: set[str] = set()
        for state, strat in mapping.items():
            if strat == strategy:
                recommended_states.add(state)

        prev_val = None
        for row in daily:
            d = row.get("date")
            v = float(row.get("value") or 0)
            if prev_val and prev_val > 0 and d in lagged_state:
                dr = v / prev_val - 1.0
                if lagged_state[d] in recommended_states:
                    in_recommended.append(dr)
                else:
                    out_recommended.append(dr)
            prev_val = v

        def _sharpe(rets: list[float]) -> float | None:
            if len(rets) < 5:
                return None
            m = sum(rets) / len(rets)
            var = sum((x - m) ** 2 for x in rets) / (len(rets) - 1)
            sd = math.sqrt(var) if var > 0 else 0
            if sd <= 0:
                return None
            return round(m / sd * math.sqrt(252), 2)

        in_sh = _sharpe(in_recommended)
        out_sh = _sharpe(out_recommended)
        lift = round(in_sh - out_sh, 2) if in_sh is not None and out_sh is not None else None
        sufficient = len(in_recommended) >= MIN_IN_REGIME_DAYS

        results.append({
            "strategy": strategy,
            "match_mode": match_key,
            "recommended_states": sorted(recommended_states),
            "in_regime_days": len(in_recommended),
            "out_regime_days": len(out_recommended),
            "in_regime_sharpe": in_sh,
            "out_regime_sharpe": out_sh,
            "sharpe_lift": lift,
            "sample_sufficient": sufficient,
            "effective": sufficient and lift is not None and lift > 0,
        })

    effective_count = sum(1 for r in results if r.get("effective"))
    tested = sum(1 for r in results if not r.get("error"))
    return {
        "backtest_days": days,
        "match_mode": match_key,
        "strategies_tested": tested,
        "effective_matches": effective_count,
        "results": results,
        "verdict": (
            f"{effective_count}/{tested} 个策略在推荐状态下夏普更高（{match_key}，in≥{MIN_IN_REGIME_DAYS}天）"
            if tested
            else "无回测结果"
        ),
    }


def generate_validation_report(
    conn: sqlite3.Connection,
    *,
    primary: str = "csi800",
    days: int = 365,
    include_strategy: bool = False,
    strategy_days: int = 180,
    include_l3_sim: bool = False,
    l3_sim_days: int = 365,
) -> dict[str, Any]:
    """生成完整验证报告 JSON。"""
    bucket_col, index_code = _bucket_col(primary)
    regime_rows = load_regime_rows(conn, primary=primary, days=days)

    kline_data = fetch_index_kline(index_code, period="daily", days=min(days + 120, 500), with_technical=False)
    kline = kline_data.get("kline") or []
    index_returns = index_returns_from_kline(kline)

    if len(regime_rows) < 60:
        causal = build_causal_regime_series(kline, use_features=False, conn=conn)
        regime_rows = causal[-days:] if len(causal) > days else causal
        data_source = "kline_recomputed_index_only"
    else:
        data_source = "market_regime_daily"

    internal = internal_consistency_report(regime_rows, index_returns)
    walk_fwd = walk_forward_report(kline, conn=conn)
    forward = forward_return_report(regime_rows, index_returns)

    report: dict[str, Any] = {
        "generated_at": date.today().isoformat(),
        "primary_index": index_code,
        "primary_bucket_column": bucket_col,
        "data_source": data_source,
        "sample_days": len(regime_rows),
        "layer1_internal_consistency": internal,
        "layer2_walk_forward": walk_fwd,
        "layer2_forward_returns": forward,
        "overall_verdict": _overall_verdict(internal, walk_fwd, forward),
    }

    if include_strategy:
        report["layer3_strategy_conditional"] = strategy_conditional_report(
            conn, regime_rows, days=strategy_days, primary=primary,
        )
        report["overall_verdict"] = _overall_verdict(
            internal, walk_fwd, forward, report["layer3_strategy_conditional"],
        )

    if include_l3_sim:
        from services.strategy_recommendation_monitor import l3_switch_simulation_report

        report["layer3_l3_switch_simulation"] = l3_switch_simulation_report(
            conn, regime_rows, days=l3_sim_days,
        )

    from services.market_regime import get_regime_agreement_stats
    report["csi300_csi800_agreement"] = get_regime_agreement_stats(conn, days=len(regime_rows))

    return report


def _overall_verdict(
    internal: dict,
    walk_fwd: dict,
    forward: dict,
    strategy: Optional[dict] = None,
) -> str:
    checks = []
    if internal.get("return_anova", {}).get("significant_05"):
        checks.append("✅ 内部一致性：收益组间差异显著")
    else:
        checks.append("⚠️ 内部一致性：收益组间差异不显著")

    b_pct = walk_fwd.get("bucket_match_rate_pct")
    if b_pct is not None and b_pct >= 60:
        checks.append(f"✅ Walk-Forward 桶匹配 {b_pct}%")
    elif b_pct is not None:
        checks.append(f"⚠️ Walk-Forward 桶匹配 {b_pct}%")

    h1 = forward.get("forward_returns", {}).get("h1", {})
    if h1.get("significant_05"):
        checks.append("✅ 前瞻1日收益可区分")
    else:
        checks.append("⚠️ 前瞻1日收益区分度不足")

    if strategy and not strategy.get("error"):
        eff = strategy.get("effective_matches", 0)
        total = strategy.get("strategies_tested", 0)
        if total and eff >= total // 2 + 1:
            checks.append(f"✅ 策略条件有效性 {eff}/{total}")
        else:
            checks.append(f"⚠️ 策略条件有效性 {eff}/{total}")

    return " | ".join(checks)


def format_validation_report_text(report: dict[str, Any]) -> str:
    """人类可读报告（CLI / 日志）。"""
    lines = [
        "📊 市场状态划分验证报告",
        "━" * 48,
        f"基准指数: {report.get('primary_index')} | 样本: {report.get('sample_days')} 天",
        f"数据源: {report.get('data_source')}",
        "",
        "【第一层 · 内部一致性】",
    ]
    l1 = report.get("layer1_internal_consistency") or {}
    for s in l1.get("bucket_stats") or []:
        mr = s.get("mean_daily_return_pct")
        mr_s = f"{mr:+.3f}%" if mr is not None else "—"
        flag = "✓" if s.get("sample_sufficient") else "⚠样本少"
        lines.append(
            f"  {s['label']:8} {s['days']:4}天  日均收益 {mr_s}  {flag}"
        )
    ra = l1.get("return_anova") or {}
    lines.append(f"  收益 ANOVA p={ra.get('p_value')} {'✅显著' if ra.get('significant_05') else '❌不显著'}")
    dwell = l1.get("dwell_time") or {}
    lines.append(f"  平均停留 {dwell.get('overall_mean_days')} 天 | {l1.get('verdict', '')}")

    lines.extend(["", "【第二层 · Walk-Forward】"])
    l2 = report.get("layer2_walk_forward") or {}
    if l2.get("error"):
        lines.append(f"  {l2['error']}")
    else:
        lines.append(f"  t-1 桶匹配率: {l2.get('bucket_match_rate_pct')}%")
        lines.append(f"  持久性基线:   {l2.get('persistence_baseline_pct')}%")
        lines.append(f"  5日多数匹配:  {l2.get('horizon5_majority_match_pct')}%")
        lines.append(f"  {l2.get('verdict', '')}")

    lines.extend(["", "【第二层 · 前瞻收益区分度】"])
    for key, block in (report.get("layer2_forward_returns") or {}).get("forward_returns", {}).items():
        sig = "✅" if block.get("significant_05") else "❌"
        lines.append(f"  {key}: ANOVA p={block.get('anova_p_value')} {sig}")

    lines.extend(["", "【CSI300 vs CSI800 一致率】"])
    agree = report.get("csi300_csi800_agreement") or {}
    if agree.get("sample_days"):
        lines.append(
            f"  标签一致 {agree.get('label_agreement_pct')}% | "
            f"四格一致 {agree.get('bucket_agreement_pct')}% "
            f"({agree.get('sample_days')} 天)"
        )

    l3 = report.get("layer3_strategy_conditional")
    if l3:
        lines.extend(["", f"【第三层 · 策略条件有效性 · {l3.get('match_mode')}】"])
        for r in l3.get("results") or []:
            if r.get("error"):
                lines.append(f"  {r['strategy']}: {r['error']}")
                continue
            lift = r.get("sharpe_lift")
            mark = "✅" if r.get("effective") else ("⚠样本" if not r.get("sample_sufficient") else "❌")
            lines.append(
                f"  {r['strategy']:18} in={r.get('in_regime_days'):3}d "
                f"夏普(in/out)={r.get('in_regime_sharpe')}/{r.get('out_regime_sharpe')} "
                f"lift={lift} {mark}"
            )
        lines.append(f"  {l3.get('verdict', '')}")

    l3sim = report.get("layer3_l3_switch_simulation")
    if l3sim:
        lines.extend(["", "【第三层 · L3 策略切换模拟】"])
        if l3sim.get("error"):
            lines.append(f"  {l3sim['error']}")
        else:
            lines.append(f"  模拟 {l3sim.get('simulation_days')} 天 · 切换 {l3sim.get('strategy_switches')} 次")
            la = l3sim.get("l3_adaptive") or {}
            sc = l3sim.get("static_composite") or {}
            hr = l3sim.get("hard_rule_only") or {}
            lines.append(
                f"  L3自适应  总收益 {la.get('total_return_pct')}%  Sharpe {la.get('sharpe')}"
            )
            lines.append(
                f"  静态综合  总收益 {sc.get('total_return_pct')}%  Sharpe {sc.get('sharpe')}"
            )
            lines.append(
                f"  硬规则    总收益 {hr.get('total_return_pct')}%  Sharpe {hr.get('sharpe')}"
            )
            lines.append(f"  {l3sim.get('verdict', '')}")

    lines.extend(["", "【综合结论】", report.get("overall_verdict", ""), ""])
    return "\n".join(lines)
