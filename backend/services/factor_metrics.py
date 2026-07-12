"""因子评估扩展指标 — 单调性 / 换手 / 显著性 / 多空曲线"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from services.ic_engine import build_factor_cross_sections, pearson, rank_values


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def ic_ttest(ics: List[float]) -> dict:
    """IC 序列均值 t 检验（双尾）。"""
    n = len(ics)
    if n < 3:
        return {"p_value": None, "t_stat": None, "n": n, "significance": None}
    mean = sum(ics) / n
    var = sum((x - mean) ** 2 for x in ics) / (n - 1)
    std = math.sqrt(var) if var > 0 else 1e-9
    se = std / math.sqrt(n)
    t_stat = mean / se if se > 0 else 0.0
    p_value = 2.0 * (1.0 - _norm_cdf(abs(t_stat)))
    sig = None
    if p_value < 0.01:
        sig = "***"
    elif p_value < 0.05:
        sig = "**"
    elif p_value < 0.1:
        sig = "*"
    return {
        "p_value": round(p_value, 4),
        "t_stat": round(t_stat, 3),
        "n": n,
        "significance": sig,
    }


def benjamini_hochberg_q(p_values: List[Optional[float]]) -> List[Optional[float]]:
    """FDR 校正（单因子时 q≈p）。"""
    indexed = [(i, p) for i, p in enumerate(p_values) if p is not None]
    if not indexed:
        return p_values
    m = len(indexed)
    sorted_idx = sorted(indexed, key=lambda x: x[1])
    q_out = list(p_values)
    prev_q = 1.0
    for rank, (orig_i, p) in enumerate(reversed(sorted_idx), start=1):
        k = m - rank + 1
        q = min(prev_q, p * m / k)
        prev_q = q
        q_out[orig_i] = round(q, 4)
    return q_out


def _spearman(x: List[float], y: List[float]) -> Optional[float]:
    if len(x) < 2 or len(y) != len(x):
        return None
    return pearson(rank_values(x), rank_values(y))


def _quantile_groups(
    pairs: List[Tuple[str, float, float]], n_groups: int
) -> List[List[float]]:
    """按因子值分 n 组，返回每组未来收益列表。pairs: code, factor, ret"""
    if len(pairs) < n_groups:
        return [[] for _ in range(n_groups)]
    sorted_pairs = sorted(pairs, key=lambda x: x[1])
    groups: List[List[float]] = [[] for _ in range(n_groups)]
    n = len(sorted_pairs)
    for i, (_, _, ret) in enumerate(sorted_pairs):
        g = min(n_groups - 1, int(i * n_groups / n))
        groups[g].append(ret)
    return groups


def compute_monotonicity(
    sections: List[dict],
    n_groups: int = 5,
) -> dict:
    """分组单调性：5 组平均收益 + Spearman(组序, 组收益)。"""
    group_sum = [0.0] * n_groups
    group_cnt = [0] * n_groups

    for sec in sections:
        pairs = [(p["code"], p["factor"], p["ret"]) for p in sec["pairs"]]
        if len(pairs) < n_groups:
            continue
        grouped = _quantile_groups(pairs, n_groups)
        for gi, rets in enumerate(grouped):
            if rets:
                group_sum[gi] += sum(rets) / len(rets)
                group_cnt[gi] += 1

    group_returns = []
    for gi in range(n_groups):
        if group_cnt[gi] > 0:
            group_returns.append(round(group_sum[gi] / group_cnt[gi], 4))
        else:
            group_returns.append(None)

    valid = [(i + 1, r) for i, r in enumerate(group_returns) if r is not None]
    if len(valid) < 3:
        return {
            "n_groups": n_groups,
            "group_returns": group_returns,
            "spearman": None,
            "monotonic": None,
            "error": "样本不足",
        }

    xs = [float(v[0]) for v in valid]
    ys = [float(v[1]) for v in valid]
    sp = _spearman(xs, ys)
    return {
        "n_groups": n_groups,
        "group_returns": group_returns,
        "spearman": round(sp, 4) if sp is not None else None,
        "monotonic": sp is not None and sp > 0.3,
        "n_periods_used": max(group_cnt),
    }


def compute_turnover(
    sections: List[dict],
    top_pct: float = 0.2,
) -> dict:
    """Top 组合相邻期换手：1 - |交集|/|前期持仓|。"""
    if len(sections) < 2:
        return {"daily_avg_turnover": None, "n_periods": 0, "top_pct": top_pct}

    turnovers: List[float] = []
    prev_top: Optional[set] = None

    for sec in sections:
        pairs = sec["pairs"]
        if len(pairs) < 5:
            continue
        sorted_pairs = sorted(pairs, key=lambda x: x["factor"], reverse=True)
        k = max(1, int(len(sorted_pairs) * top_pct))
        top = {p["code"] for p in sorted_pairs[:k]}
        if prev_top is not None:
            if len(prev_top) > 0:
                overlap = len(prev_top & top)
                turnovers.append(1.0 - overlap / len(prev_top))
        prev_top = top

    if not turnovers:
        return {"daily_avg_turnover": None, "n_periods": 0, "top_pct": top_pct}

    avg = sum(turnovers) / len(turnovers)
    return {
        "daily_avg_turnover": round(avg, 4),
        "n_periods": len(turnovers),
        "top_pct": top_pct,
    }


def compute_long_short_curve(
    sections: List[dict],
    top_pct: float = 0.2,
    max_points: int = 60,
) -> dict:
    """做多 Top / 做空 Bottom 组合累积净值。"""
    curve: List[dict] = []
    nav = 1.0

    for sec in sections:
        pairs = sec["pairs"]
        if len(pairs) < 5:
            continue
        sorted_pairs = sorted(pairs, key=lambda x: x["factor"], reverse=True)
        k = max(1, int(len(sorted_pairs) * top_pct))
        top_rets = [p["ret"] for p in sorted_pairs[:k]]
        bot_rets = [p["ret"] for p in sorted_pairs[-k:]]
        long_ret = sum(top_rets) / len(top_rets)
        short_ret = sum(bot_rets) / len(bot_rets)
        ls_ret = long_ret - short_ret
        nav *= 1.0 + ls_ret / 100.0
        curve.append(
            {
                "date": sec["date"],
                "long_ret": round(long_ret, 4),
                "short_ret": round(short_ret, 4),
                "ls_ret": round(ls_ret, 4),
                "nav": round(nav, 4),
            }
        )

    if len(curve) > max_points:
        curve = curve[-max_points:]

    total_ls = round((curve[-1]["nav"] - 1.0) * 100, 2) if curve else None
    return {
        "top_pct": top_pct,
        "cumulative": curve,
        "total_return_pct": total_ls,
        "n_periods": len(curve),
    }


def analyze_factor_metrics(
    factor_id: str,
    forward_days: int = 20,
    n_groups: int = 5,
    top_pct: float = 0.2,
    max_dates: Optional[int] = 60,
) -> dict:
    """汇总 S1 扩展指标。"""
    sections, ic_summary = build_factor_cross_sections(
        factor_id, forward_days=forward_days, max_dates=max_dates
    )
    if not sections:
        return {
            "factor_id": factor_id,
            "forward_days": forward_days,
            "error": ic_summary.get("error", "无截面数据"),
        }

    ics = [x["ic"] for x in ic_summary.get("ic_series", [])]
    ttest = ic_ttest(ics)
    q_list = benjamini_hochberg_q([ttest.get("p_value")])
    ttest["fdr_q"] = q_list[0] if q_list else None

    mono = compute_monotonicity(sections, n_groups=n_groups)
    turnover = compute_turnover(sections, top_pct=top_pct)
    long_short = compute_long_short_curve(sections, top_pct=top_pct, max_points=max_dates or 60)

    return {
        "factor_id": factor_id,
        "forward_days": forward_days,
        "monotonicity": mono,
        "turnover": turnover,
        "ic_significance": ttest,
        "long_short": long_short,
        "n_cross_sections": len(sections),
    }
