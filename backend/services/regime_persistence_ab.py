"""P3-A：Regime Persistence A/B 对照（内存模拟，默认不写库）。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Optional

import config
from services.market_regime import (
    REGIME_BUCKET_ORDER,
    apply_regime_persistence,
    regime_bucket,
    regime_bucket_label,
    recompute_regime_persistence,
)
from services.regime_validation import (
    compute_dwell_times,
    index_returns_from_kline,
    internal_consistency_report,
)
from services.strategy_recommendation_monitor import l3_switch_simulation_report


@dataclass(frozen=True)
class PersistenceVariant:
    id: str
    label: str
    asymmetric: bool
    symmetric_days: int = 5
    up_days: int = 5
    down_days: int = 2
    vol_days: int = 3
    osc_days: int = 3

    def min_days_for_regime(self, regime: str) -> int:
        if not self.asymmetric:
            return max(1, self.symmetric_days)
        bucket = regime_bucket(str(regime or "oscillation"))
        if bucket == "trend_up":
            return max(1, self.up_days)
        if bucket == "trend_down":
            return max(1, self.down_days)
        if bucket == "high_vol":
            return max(1, self.vol_days)
        return max(1, self.osc_days)

    def to_config_dict(self) -> dict[str, Any]:
        if self.asymmetric:
            return {
                "asymmetric": True,
                "confirm_days": {
                    "trend_up": self.up_days,
                    "trend_down": self.down_days,
                    "high_vol": self.vol_days,
                    "oscillation": self.osc_days,
                },
            }
        return {"asymmetric": False, "symmetric_days": self.symmetric_days}


def production_variant() -> PersistenceVariant:
    if config.REGIME_ASYMMETRIC_PERSISTENCE:
        return PersistenceVariant(
            id="asymmetric_prod",
            label="不对称（生产默认）",
            asymmetric=True,
            up_days=config.REGIME_UP_CONFIRM_DAYS,
            down_days=config.REGIME_DOWN_CONFIRM_DAYS,
            vol_days=config.REGIME_VOL_CONFIRM_DAYS,
            osc_days=config.REGIME_OSC_CONFIRM_DAYS,
        )
    return PersistenceVariant(
        id="symmetric_prod",
        label=f"对称 {config.REGIME_PERSISTENCE_DAYS} 日（生产默认）",
        asymmetric=False,
        symmetric_days=config.REGIME_PERSISTENCE_DAYS,
    )


DEFAULT_VARIANTS: tuple[PersistenceVariant, ...] = (
    PersistenceVariant(
        id="symmetric_5",
        label="对称 5 日",
        asymmetric=False,
        symmetric_days=5,
    ),
    production_variant(),
    PersistenceVariant(
        id="symmetric_3",
        label="对称 3 日",
        asymmetric=False,
        symmetric_days=3,
    ),
)


def variant_by_id(variant_id: str) -> Optional[PersistenceVariant]:
    for v in DEFAULT_VARIANTS:
        if v.id == variant_id:
            return v
    if variant_id == "asymmetric":
        return production_variant()
    return None


def load_raw_regime_series(conn, *, days: int = 730) -> dict[str, Any]:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(market_regime_daily)").fetchall()}
    if "regime_csi800_raw" not in cols:
        return {"error": "缺少 regime_*_raw 列，请先 migration v58 + 回填"}

    rows = conn.execute(
        """SELECT trade_date, regime_raw, regime_csi800_raw,
                  price_vs_ma60, price_vs_ma60_csi800, volatility_20, volatility_20_csi800
           FROM market_regime_daily
           WHERE regime_csi800 IS NOT NULL OR regime IS NOT NULL
           ORDER BY trade_date DESC LIMIT ?""",
        (days,),
    ).fetchall()
    if not rows:
        return {"error": "market_regime_daily 样本不足"}

    ordered = list(reversed(rows))
    dates = [r[0] for r in ordered]
    raw300 = [str(r[1] or "oscillation") for r in ordered]
    raw800 = [str(r[2] or r[1] or "oscillation") for r in ordered]
    pv300 = [float(r[3] or 0) for r in ordered]
    pv800 = [float(r[4] if r[4] is not None else r[3] or 0) for r in ordered]
    vol = [
        float(r[6] if r[6] is not None else r[5] or 0)
        for r in ordered
    ]

    raw_buckets = [regime_bucket(raw800[i], pv800[i]) for i in range(len(dates))]
    raw_dist: dict[str, int] = {b: 0 for b in REGIME_BUCKET_ORDER}
    for b in raw_buckets:
        raw_dist[b] = raw_dist.get(b, 0) + 1

    return {
        "dates": dates,
        "raw300": raw300,
        "raw800": raw800,
        "pv300": pv300,
        "pv800": pv800,
        "volatility_20": vol,
        "raw_bucket_distribution": raw_dist,
        "sample_days": len(dates),
        "start_date": dates[0],
        "end_date": dates[-1],
    }


def confirmed_buckets_for_variant(series: dict[str, Any], variant: PersistenceVariant) -> list[str]:
    raw800 = series["raw800"]
    pv800 = series["pv800"]
    fn: Callable[[str], int] = variant.min_days_for_regime
    confirmed_regimes = apply_regime_persistence(
        raw800,
        min_days=variant.symmetric_days,
        min_days_for=fn if variant.asymmetric else None,
    )
    return [regime_bucket(confirmed_regimes[i], pv800[i]) for i in range(len(raw800))]


def regime_rows_from_buckets(series: dict[str, Any], buckets: list[str]) -> list[dict[str, Any]]:
    rows = []
    for i, td in enumerate(series["dates"]):
        rows.append({
            "trade_date": td,
            "bucket": buckets[i],
            "volatility_20": series["volatility_20"][i],
            "price_vs_ma60_csi800": series["pv800"][i],
        })
    return rows


def _trend_segments(buckets: list[str], dates: list[str], *, target: str) -> list[dict[str, Any]]:
    segs: list[dict[str, Any]] = []
    i = 0
    n = len(buckets)
    while i < n:
        if buckets[i] != target:
            i += 1
            continue
        j = i + 1
        while j < n and buckets[j] == target:
            j += 1
        segs.append({
            "bucket": target,
            "bucket_label": regime_bucket_label(target),
            "start_date": dates[i],
            "end_date": dates[j - 1],
            "days": j - i,
        })
        i = j
    return segs


def evaluate_variant(
    conn,
    variant: PersistenceVariant,
    series: dict[str, Any],
    *,
    index_returns: dict[str, float],
    l3_sim_days: int = 365,
    run_l3_sim: bool = True,
) -> dict[str, Any]:
    buckets = confirmed_buckets_for_variant(series, variant)
    dates = series["dates"]
    dist: dict[str, int] = {b: 0 for b in REGIME_BUCKET_ORDER}
    for b in buckets:
        dist[b] = dist.get(b, 0) + 1

    dwell = compute_dwell_times(buckets)
    regime_rows = regime_rows_from_buckets(series, buckets)
    internal = internal_consistency_report(regime_rows, index_returns)

    bucket_transitions = sum(
        1 for i in range(1, len(buckets)) if buckets[i] != buckets[i - 1]
    )

    trend_up_segs = _trend_segments(buckets, dates, target="trend_up")
    trend_down_segs = _trend_segments(buckets, dates, target="trend_down")

    l3_sim: dict[str, Any] = {"skipped": True}
    if run_l3_sim:
        l3_sim = l3_switch_simulation_report(
            conn, regime_rows, days=l3_sim_days,
        )

    return {
        "variant_id": variant.id,
        "variant_label": variant.label,
        "config": variant.to_config_dict(),
        "distribution": dist,
        "distribution_pct": {
            b: round(dist[b] / max(len(buckets), 1) * 100, 1) for b in REGIME_BUCKET_ORDER
        },
        "dwell_time": dwell,
        "bucket_transitions": bucket_transitions,
        "trend_up_segments": trend_up_segs,
        "trend_down_segments": trend_down_segs,
        "trend_up_max_segment_days": max((s["days"] for s in trend_up_segs), default=0),
        "trend_down_max_segment_days": max((s["days"] for s in trend_down_segs), default=0),
        "internal_consistency": {
            "return_anova_p": internal.get("return_anova", {}).get("p_value"),
            "return_anova_significant": internal.get("return_anova", {}).get("significant_05"),
            "volatility_anova_significant": internal.get("volatility_anova", {}).get("significant_05"),
            "verdict": internal.get("verdict"),
        },
        "l3_simulation": l3_sim,
    }


def score_variant(result: dict[str, Any]) -> float:
    """启发式综合分（越高越好）。"""
    score = 0.0
    dist = result.get("distribution") or {}
    td_days = dist.get("trend_down") or 0
    tu_days = dist.get("trend_up") or 0

    if td_days >= 10:
        score += 2.0
    elif td_days >= 5:
        score += 1.0
    else:
        score -= 1.0

    if tu_days >= 20:
        score += 1.0

    dwell = (result.get("dwell_time") or {}).get("overall_mean_days") or 0
    score += min(dwell / 8.0, 2.0)

    internal = result.get("internal_consistency") or {}
    if internal.get("return_anova_significant"):
        score += 1.0

    l3 = result.get("l3_simulation") or {}
    if not l3.get("error") and not l3.get("skipped"):
        switches = l3.get("strategy_switches") or 0
        score -= max(0, switches - 8) * 0.15
        lift = l3.get("sharpe_lift_vs_composite")
        if lift is not None:
            score += lift * 0.5
        l3_sh = (l3.get("l3_adaptive") or {}).get("sharpe")
        comp_sh = (l3.get("static_composite") or {}).get("sharpe")
        if l3_sh is not None and comp_sh is not None and l3_sh >= comp_sh:
            score += 0.5

    transitions = result.get("bucket_transitions") or 0
    sample = sum(dist.values()) or 1
    trans_rate = transitions / sample
    if trans_rate > 0.08:
        score -= (trans_rate - 0.08) * 20

    return round(score, 2)


def rank_variants(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = []
    for r in results:
        ranked.append({**r, "score": score_variant(r)})
    ranked.sort(key=lambda x: -x["score"])
    for i, r in enumerate(ranked):
        r["rank"] = i + 1
    return ranked


def compare_persistence_variants(
    conn,
    *,
    variants: Optional[list[PersistenceVariant]] = None,
    days: int = 730,
    l3_sim_days: int = 365,
    run_l3_sim: bool = True,
) -> dict[str, Any]:
    series = load_raw_regime_series(conn, days=days)
    if series.get("error"):
        return {"error": series["error"]}

    from services.market_index import fetch_index_kline

    kline = fetch_index_kline(
        config.REGIME_INDEX_CSI800,
        period="daily",
        days=min(days + 120, 800),
        with_technical=False,
    )
    index_returns = index_returns_from_kline(kline.get("kline") or [])

    vars_ = variants or list(DEFAULT_VARIANTS)
    results = [
        evaluate_variant(
            conn, v, series,
            index_returns=index_returns,
            l3_sim_days=l3_sim_days,
            run_l3_sim=run_l3_sim,
        )
        for v in vars_
    ]
    ranked = rank_variants(results)
    winner = ranked[0] if ranked else None

    return {
        "generated_at": date.today().isoformat(),
        "sample_days": series["sample_days"],
        "start_date": series["start_date"],
        "end_date": series["end_date"],
        "raw_bucket_distribution": series["raw_bucket_distribution"],
        "variants_tested": [v.id for v in vars_],
        "results": ranked,
        "winner": {
            "variant_id": winner["variant_id"],
            "variant_label": winner["variant_label"],
            "score": winner["score"],
            "config": winner["config"],
        } if winner else None,
        "note": "内存模拟，未写库；矩阵 as_of 固定为当前 L2，L3 模拟仅反映 persistence 差异",
    }


def apply_variant_to_db(conn, variant: PersistenceVariant, *, days: int = 730) -> dict[str, Any]:
    """将选定 variant 写回 market_regime_daily（--apply）。"""
    fn: Callable[[str], int] = variant.min_days_for_regime
    return recompute_regime_persistence(
        conn,
        days=days,
        min_days=variant.symmetric_days,
        asymmetric=variant.asymmetric,
        min_days_for_fn=fn if variant.asymmetric else None,
    )


def format_ab_report_text(report: dict[str, Any]) -> str:
    lines = [
        "📊 Regime Persistence A/B 对照",
        "━" * 52,
        f"样本: {report.get('start_date')} → {report.get('end_date')} ({report.get('sample_days')} 天)",
        f"raw 分布: {report.get('raw_bucket_distribution')}",
        "",
    ]
    for r in report.get("results") or []:
        lines.append(f"【#{r.get('rank')} {r.get('variant_label')}】 score={r.get('score')}")
        lines.append(f"  配置: {r.get('config')}")
        lines.append(f"  四格: {r.get('distribution')}")
        dwell = r.get("dwell_time") or {}
        lines.append(
            f"  停留: 均 {dwell.get('overall_mean_days')} 天 · "
            f"切换 {r.get('bucket_transitions')} 次 · "
            f"trend_down {r.get('distribution', {}).get('trend_down', 0)} 天"
        )
        td_segs = r.get("trend_down_segments") or []
        if td_segs:
            top = max(td_segs, key=lambda s: s["days"])
            lines.append(
                f"  trend_down 最长段: {top['start_date']}→{top['end_date']} ({top['days']}天)"
            )
        l3 = r.get("l3_simulation") or {}
        if not l3.get("skipped") and not l3.get("error"):
            la = l3.get("l3_adaptive") or {}
            sc = l3.get("static_composite") or {}
            lines.append(
                f"  L3模拟: Sharpe {la.get('sharpe')} vs composite {sc.get('sharpe')} "
                f"(切换 {l3.get('strategy_switches')} 次)"
            )
        internal = r.get("internal_consistency") or {}
        lines.append(f"  收益 ANOVA p={internal.get('return_anova_p')} · {internal.get('verdict', '')[:60]}")
        lines.append("")

    w = report.get("winner")
    if w:
        lines.append(f"🏆 推荐: {w.get('variant_label')} ({w.get('variant_id')}) score={w.get('score')}")
        lines.append("  写库: python scripts/compare_regime_persistence_ab.py --apply " + w["variant_id"])
    lines.append("")
    lines.append(report.get("note", ""))
    return "\n".join(lines)
