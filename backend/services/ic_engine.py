"""统一 IC 引擎 — 因子值 vs 未来 N 日股票收益率"""
from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from config import DB_PATH
from services.v5_scorer import V5_LABELS
from services.v5_score_query import _parse_dim_score

V5_IC_COLUMNS = ["composite_v5", "quality_score", "industry_score", "market_env_score"]
V5_IC_DIMS = list(V5_LABELS.keys())

SCORE_FACTOR_MAP = {
    "composite_v5": "F001",
    "quality_score": "F009",
    "industry_score": "F010",
    "market_env_score": "F011",
    **{dim: f"V5_{dim}" for dim in V5_IC_DIMS},
}

FACTOR_ID_TO_SCORE = {v: k for k, v in SCORE_FACTOR_MAP.items()}


def pearson(x: List[float], y: List[float]) -> Optional[float]:
    n = len(x)
    if n < 5 or len(y) != n:
        return None
    mx = sum(x) / n
    my = sum(y) / n
    sx = math.sqrt(sum((v - mx) ** 2 for v in x) / (n - 1))
    sy = math.sqrt(sum((v - my) ** 2 for v in y) / (n - 1))
    if sx == 0 or sy == 0:
        return 0.0
    cov = sum((x[i] - mx) * (y[i] - my) for i in range(n)) / (n - 1)
    return round(cov / (sx * sy), 4)


def rank_values(vals: List[float]) -> List[float]:
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    for r, i in enumerate(order):
        ranks[i] = r + 1
    return ranks


def rank_ic(x: List[float], y: List[float]) -> Optional[float]:
    if len(x) < 5 or len(y) != len(x):
        return None
    return pearson(rank_values(x), rank_values(y))


def _load_lifecycle_cache() -> Dict[int, tuple]:
    from services.stock_lifecycle import sync_lifecycle_from_stocks

    conn = sqlite3.connect(DB_PATH)
    sync_lifecycle_from_stocks(conn)
    rows = conn.execute(
        "SELECT stock_id, list_date, delist_date FROM stock_lifecycle"
    ).fetchall()
    conn.close()
    return {r[0]: (r[1], r[2]) for r in rows}


def _is_alive(stock_id: int, as_of_date: str, lifecycle: Dict[int, tuple]) -> bool:
    from services.stock_lifecycle import is_alive

    return is_alive(stock_id, as_of_date, lifecycle)


def _load_price_forward_returns(forward_days: int = 20) -> Dict[str, Dict[str, float]]:
    """code -> trade_date -> forward return (%)，使用后复权/前复权 adj_close。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    from services.data_cleaner import ensure_quote_columns

    ensure_quote_columns(conn)
    rows = conn.execute(
        """SELECT s.code, q.trade_date,
                  COALESCE(q.adj_close, q.close) AS px
           FROM stock_daily_quotes q
           JOIN stocks s ON q.stock_id = s.id
           WHERE COALESCE(q.adj_close, q.close) IS NOT NULL
             AND COALESCE(q.is_suspended, 0) = 0
           ORDER BY s.code, q.trade_date"""
    ).fetchall()
    conn.close()

    by_code: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
    for r in rows:
        by_code[r["code"]].append((r["trade_date"], float(r["px"])))

    out: Dict[str, Dict[str, float]] = defaultdict(dict)
    for code, series in by_code.items():
        for i, (dt, px) in enumerate(series):
            j = i + forward_days
            if j >= len(series):
                continue
            p_fwd = series[j][1]
            if px > 0:
                out[code][dt] = round((p_fwd / px - 1) * 100, 4)
    return out


def _nearest_score_on_or_before(score_snap: dict, code: str, dt: str) -> Optional[float]:
    if code not in score_snap:
        return None
    best = None
    for sd in sorted(score_snap[code].keys()):
        if sd <= dt:
            best = sd
    if best is None and score_snap[code]:
        best = sorted(score_snap[code].keys())[0]
    return score_snap[code].get(best) if best else None


def analyze_v5_dim_column(
    dim: str,
    forward_days: int = 20,
    max_dates: Optional[int] = None,
) -> dict:
    """V5 十维档位 IC：从 v5_breakdown_json 解析 dim_scores。"""
    lifecycle = _load_lifecycle_cache()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT cs.stock_id, s.code, cs.calc_date, cs.v5_breakdown_json
           FROM comprehensive_scores cs
           JOIN stocks s ON cs.stock_id = s.id
           WHERE cs.v5_breakdown_json IS NOT NULL
           ORDER BY cs.calc_date"""
    ).fetchall()
    conn.close()

    if not rows:
        return {"factor": dim, "error": "无 V5 评分数据"}

    by_date: Dict[str, List[Tuple[float, float]]] = defaultdict(list)
    fwd = _load_price_forward_returns(forward_days)

    for r in rows:
        sid = r["stock_id"]
        code = r["code"]
        dt = r["calc_date"]
        if not _is_alive(sid, dt, lifecycle):
            continue
        val = _parse_dim_score(r["v5_breakdown_json"], dim)
        if val is None:
            continue
        ret = fwd.get(code, {}).get(dt)
        if ret is None:
            continue
        by_date[dt].append((float(val), ret))

    ic_series = []
    rank_series = []
    for dt in sorted(by_date.keys()):
        pairs = by_date[dt]
        if len(pairs) < 5:
            continue
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        ic = pearson(xs, ys)
        ric = rank_ic(xs, ys)
        if ic is not None:
            ic_series.append({"date": dt, "ic": ic})
        if ric is not None:
            rank_series.append({"date": dt, "rank_ic": ric})

    if max_dates and len(ic_series) > max_dates:
        ic_series = ic_series[-max_dates:]
        rank_series = rank_series[-max_dates:]

    label = V5_LABELS.get(dim, dim)
    out = _summarize_ic(label, ic_series, rank_series, forward_days)
    out["factor"] = dim
    out["survivorship_adjusted"] = True
    return out


def analyze_score_column(
    factor_col: str,
    forward_days: int = 20,
    max_dates: Optional[int] = None,
) -> dict:
    """V5 评分列 IC：因子分_t vs 未来 forward_days 日收益率（幸存者偏差校正）。"""
    lifecycle = _load_lifecycle_cache()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"""SELECT cs.stock_id, s.code, cs.calc_date, cs.{factor_col} AS factor_val
            FROM comprehensive_scores cs
            JOIN stocks s ON cs.stock_id = s.id
            WHERE cs.{factor_col} IS NOT NULL
            ORDER BY cs.calc_date"""
    ).fetchall()
    conn.close()

    if not rows:
        return {"factor": factor_col, "error": "无评分数据"}

    by_date: Dict[str, List[Tuple[float, float]]] = defaultdict(list)
    fwd = _load_price_forward_returns(forward_days)

    for r in rows:
        sid = r["stock_id"]
        code = r["code"]
        dt = r["calc_date"]
        if not _is_alive(sid, dt, lifecycle):
            continue
        ret = fwd.get(code, {}).get(dt)
        if ret is None:
            continue
        by_date[dt].append((float(r["factor_val"]), ret))

    ic_series = []
    rank_series = []
    for dt in sorted(by_date.keys()):
        pairs = by_date[dt]
        if len(pairs) < 5:
            continue
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        ic = pearson(xs, ys)
        ric = rank_ic(xs, ys)
        if ic is not None:
            ic_series.append({"date": dt, "ic": ic})
        if ric is not None:
            rank_series.append({"date": dt, "rank_ic": ric})

    if max_dates and len(ic_series) > max_dates:
        ic_series = ic_series[-max_dates:]
        rank_series = rank_series[-max_dates:]

    out = _summarize_ic(factor_col, ic_series, rank_series, forward_days)
    out["survivorship_adjusted"] = True
    return out


def build_factor_cross_sections(
    factor_id: str,
    forward_days: int = 20,
    max_dates: Optional[int] = None,
) -> Tuple[List[dict], dict]:
    """
    按日截面：{date, pairs: [{stock_id, code, factor, ret}, ...]}。
    返回 (sections, ic_summary)。
    """
    lifecycle = _load_lifecycle_cache()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT fv.stock_id, s.code, fv.date, fv.value
           FROM factor_values fv
           JOIN stocks s ON fv.stock_id = s.id
           WHERE fv.factor_id = ? AND fv.value IS NOT NULL
           ORDER BY fv.date""",
        (factor_id,),
    ).fetchall()
    conn.close()

    if not rows:
        return [], {"factor_id": factor_id, "error": "无因子数据", "ic_series": []}

    fwd = _load_price_forward_returns(forward_days)
    by_date: Dict[str, List[dict]] = defaultdict(list)

    for r in rows:
        sid = r["stock_id"]
        dt = r["date"]
        if not _is_alive(sid, dt, lifecycle):
            continue
        ret = fwd.get(r["code"], {}).get(dt)
        if ret is None:
            continue
        by_date[dt].append(
            {
                "stock_id": sid,
                "code": r["code"],
                "factor": float(r["value"]),
                "ret": float(ret),
            }
        )

    dates = sorted(by_date.keys())
    if max_dates and len(dates) > max_dates:
        dates = dates[-max_dates:]

    sections = []
    ic_series = []
    rank_series = []
    for dt in dates:
        pairs = by_date[dt]
        if len(pairs) < 5:
            continue
        sections.append({"date": dt, "pairs": pairs})
        xs = [p["factor"] for p in pairs]
        ys = [p["ret"] for p in pairs]
        ic = pearson(xs, ys)
        ric = rank_ic(xs, ys)
        if ic is not None:
            ic_series.append({"date": dt, "ic": ic})
        if ric is not None:
            rank_series.append({"date": dt, "rank_ic": ric})

    label = FACTOR_ID_TO_SCORE.get(factor_id, factor_id)
    summary = _summarize_ic(label, ic_series, rank_series, forward_days)
    summary["factor_id"] = factor_id
    summary["survivorship_adjusted"] = True
    return sections, summary


def analyze_factor_id(factor_id: str, forward_days: int = 20, max_dates: Optional[int] = None) -> dict:
    """factor_values / wide 表因子 IC（含生命周期过滤）。"""
    _sections, summary = build_factor_cross_sections(
        factor_id, forward_days=forward_days, max_dates=max_dates
    )
    if "error" in summary and not summary.get("n_periods"):
        return summary
    return summary


def analyze_ic_heatmap(
    forward_days_list: Optional[List[int]] = None,
    period: int = 60,
) -> dict:
    """V5 因子 × 未来收益天数 IC 热力矩阵"""
    forward_days_list = forward_days_list or [5, 10, 20, 60]
    keys = V5_IC_COLUMNS + V5_IC_DIMS
    matrix: Dict[str, Dict[str, float]] = {}
    for key in keys:
        matrix[key] = {}
        for fd in forward_days_list:
            if key in V5_IC_COLUMNS:
                r = analyze_score_column(key, forward_days=fd, max_dates=period)
            else:
                r = analyze_v5_dim_column(key, forward_days=fd, max_dates=period)
            matrix[key][str(fd)] = r.get("mean_ic", 0) if "error" not in r else 0
    return {"period": period, "forward_days": forward_days_list, "matrix": matrix}


def analyze_all_score_factors(forward_days: int = 20, period: int = 60) -> dict:
    """批量分析 V5 综合/因子列 + 十维档位 IC。"""
    results = {}
    for col in V5_IC_COLUMNS:
        r = analyze_score_column(col, forward_days=forward_days, max_dates=period)
        if "error" not in r:
            results[col] = {
                "mean_ic": r["mean_ic"],
                "mean_rank_ic": r.get("mean_rank_ic", 0),
                "ir": r["ir"],
                "ic_positive_ratio": r["ic_positive_ratio"],
                "effectiveness": r["effectiveness"],
                "n_periods": r["n_periods"],
                "forward_days": forward_days,
            }
    for dim in V5_IC_DIMS:
        r = analyze_v5_dim_column(dim, forward_days=forward_days, max_dates=period)
        if "error" not in r:
            results[dim] = {
                "mean_ic": r["mean_ic"],
                "mean_rank_ic": r.get("mean_rank_ic", 0),
                "ir": r["ir"],
                "ic_positive_ratio": r["ic_positive_ratio"],
                "effectiveness": r["effectiveness"],
                "n_periods": r["n_periods"],
                "forward_days": forward_days,
            }
    ranked = sorted(results.items(), key=lambda x: -abs(x[1]["ir"]))
    from services.beta_health import attach_meta

    return attach_meta({"forward_days": forward_days, "period": period, "factors": {k: v for k, v in ranked}})


def factor_layer_forward_returns(factor_id: str, forward_days: int = 20) -> dict:
    """分层：按因子 Top/Bottom 20% 比较未来 forward_days 日平均收益 (%)。 """
    lifecycle = _load_lifecycle_cache()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    latest = conn.execute(
        "SELECT MAX(date) FROM factor_values WHERE factor_id = ?", (factor_id,)
    ).fetchone()[0]
    if not latest:
        conn.close()
        return {"factor_id": factor_id, "error": "无因子数据"}

    rows = conn.execute(
        """SELECT fv.stock_id, s.code, fv.value
           FROM factor_values fv JOIN stocks s ON fv.stock_id = s.id
           WHERE fv.factor_id = ? AND fv.date = ?
           ORDER BY fv.value DESC""",
        (factor_id, latest),
    ).fetchall()
    conn.close()

    if not rows:
        return {"factor_id": factor_id, "error": "无因子数据"}

    fwd = _load_price_forward_returns(forward_days)
    scored = []
    for r in rows:
        if not _is_alive(r["stock_id"], latest, lifecycle):
            continue
        ret = fwd.get(r["code"], {}).get(latest)
        if ret is not None:
            scored.append((r["code"], float(r["value"]), ret))

    if len(scored) < 5:
        return {"factor_id": factor_id, "error": "未来收益样本不足", "date": latest}

    scored.sort(key=lambda x: -x[1])
    k = max(1, len(scored) // 5)
    top = scored[:k]
    bottom = scored[-k:]
    top_avg = round(sum(x[2] for x in top) / len(top), 2)
    bottom_avg = round(sum(x[2] for x in bottom) / len(bottom), 2)
    return {
        "factor_id": factor_id,
        "date": latest,
        "forward_days": forward_days,
        "total_stocks": len(scored),
        "top_avg": top_avg,
        "bottom_avg": bottom_avg,
        "spread": round(top_avg - bottom_avg, 2),
        "top_codes": [x[0] for x in top[:5]],
        "bottom_codes": [x[0] for x in bottom[:5]],
        "note": f"Top/Bottom 20% 未来{forward_days}日平均收益(%)",
    }


def analyze_factor_decay(
    factor_id: str,
    forward_days: int = 20,
    lags: Optional[List[int]] = None,
) -> dict:
    """IC 衰减：lag(1,5,10,20) 日因子值 vs 未来收益"""
    lags = lags or [1, 5, 10, 20]
    lifecycle = _load_lifecycle_cache()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT fv.stock_id, s.code, fv.date, fv.value
           FROM factor_values fv JOIN stocks s ON fv.stock_id = s.id
           WHERE fv.factor_id = ? AND fv.value IS NOT NULL
           ORDER BY fv.date""",
        (factor_id,),
    ).fetchall()
    conn.close()

    if not rows:
        return {"factor_id": factor_id, "error": "无因子数据"}

    distinct_dates = sorted({r["date"] for r in rows})
    if len(distinct_dates) < 20:
        return {
            "factor_id": factor_id,
            "error": "insufficient_sample",
            "reason": f"仅 {len(distinct_dates)} 个交易日，需 ≥20",
            "n_dates": len(distinct_dates),
        }

    by_code_date: Dict[str, Dict[str, float]] = defaultdict(dict)
    alive_by_code_date: Dict[str, set] = defaultdict(set)
    for r in rows:
        if not _is_alive(r["stock_id"], r["date"], lifecycle):
            continue
        by_code_date[r["code"]][r["date"]] = float(r["value"])
        alive_by_code_date[r["code"]].add(r["date"])

    fwd = _load_price_forward_returns(forward_days)
    decay = []
    for lag in lags:
        ic_series = []
        for dt in distinct_dates[lag:]:
            lag_idx = distinct_dates.index(dt) - lag
            if lag_idx < 0:
                continue
            lag_dt = distinct_dates[lag_idx]
            pairs = []
            for code, series in by_code_date.items():
                fv = series.get(lag_dt)
                ret = fwd.get(code, {}).get(dt)
                if fv is not None and ret is not None:
                    pairs.append((fv, ret))
            if len(pairs) >= 5:
                ic = pearson([p[0] for p in pairs], [p[1] for p in pairs])
                if ic is not None:
                    ic_series.append(ic)
        mean_ic = round(sum(ic_series) / len(ic_series), 4) if ic_series else None
        decay.append({"lag": lag, "mean_ic": mean_ic, "n_periods": len(ic_series)})

    base = analyze_factor_id(factor_id, forward_days=forward_days, max_dates=60)
    return {
        "factor_id": factor_id,
        "forward_days": forward_days,
        "lags": decay,
        "baseline_mean_ic": base.get("mean_ic"),
        "baseline_ir": base.get("ir"),
        "n_dates": len(distinct_dates),
    }


def _summarize_ic(label: str, ic_series: list, rank_series: list, forward_days: int) -> dict:
    if not ic_series:
        return {"factor": label, "error": "IC 样本不足", "forward_days": forward_days}

    ics = [x["ic"] for x in ic_series]
    mean_ic = sum(ics) / len(ics)
    std_ic = math.sqrt(sum((x - mean_ic) ** 2 for x in ics) / (len(ics) - 1)) if len(ics) > 1 else 0.01
    ir = mean_ic / std_ic if std_ic > 0 else 0
    pos = sum(1 for x in ics if x > 0) / len(ics)

    rank_ics = [x["rank_ic"] for x in rank_series] if rank_series else []
    mean_rank = sum(rank_ics) / len(rank_ics) if rank_ics else 0

    eff = "strong" if abs(ir) > 0.5 else ("weak" if abs(ir) < 0.1 else "moderate")
    return {
        "factor": label,
        "forward_days": forward_days,
        "mean_ic": round(mean_ic, 4),
        "mean_rank_ic": round(mean_rank, 4),
        "ir": round(ir, 2),
        "ic_positive_ratio": round(pos, 2),
        "effectiveness": eff,
        "n_periods": len(ic_series),
        "ic_series": ic_series,
        "rank_ic_series": rank_series,
    }


V5_IC_HORIZONS = (5, 20, 60)
V5_IC_BLEND = {5: 0.3, 20: 0.4, 60: 0.3}


def compute_v5_dimension_ic(
    dimension: str = "composite_v5",
    *,
    horizons: tuple[int, ...] = V5_IC_HORIZONS,
    max_dates: int = 60,
) -> dict:
    """V5 多周期 IC：5/20/60 日合成。"""
    import config

    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            f"""SELECT cs.stock_id, s.code, cs.calc_date, cs.{dimension} AS value
                FROM comprehensive_scores cs
                JOIN stocks s ON s.id = cs.stock_id
                WHERE cs.{dimension} IS NOT NULL AND s.is_active=1
                ORDER BY cs.calc_date""",
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {"dimension": dimension, "error": "无 V5 分数历史"}

    by_horizon: dict[int, list] = {}
    for h in horizons:
        fwd = _load_price_forward_returns(h)
        lifecycle = _load_lifecycle_cache()
        ic_series = []
        dates = sorted({r["calc_date"] for r in rows})[-max_dates:]
        by_code_date: dict[str, dict[str, float]] = defaultdict(dict)
        for r in rows:
            if r["calc_date"] not in dates:
                continue
            if not _is_alive(r["stock_id"], r["calc_date"], lifecycle):
                continue
            by_code_date[r["code"]][r["calc_date"]] = float(r["value"])

        for dt in dates:
            pairs = []
            for code, series in by_code_date.items():
                fv = series.get(dt)
                ret = fwd.get(code, {}).get(dt)
                if fv is not None and ret is not None:
                    pairs.append((fv, ret))
            if len(pairs) >= 5:
                ic = pearson([p[0] for p in pairs], [p[1] for p in pairs])
                ric = rank_ic([p[0] for p in pairs], [p[1] for p in pairs])
                if ic is not None:
                    ic_series.append({"date": dt, "ic": ic, "rank_ic": ric or ic})
        by_horizon[h] = ic_series

    horizon_stats = {}
    composite = 0.0
    blend_w = 0.0
    for h, series in by_horizon.items():
        if not series:
            horizon_stats[h] = {"mean_ic": None, "n_periods": 0}
            continue
        ics = [x["ic"] for x in series]
        mean_ic = sum(ics) / len(ics)
        pos = sum(1 for x in ics if x > 0) / len(ics)
        horizon_stats[h] = {
            "mean_ic": round(mean_ic, 4),
            "ic_positive_ratio": round(pos, 2),
            "n_periods": len(series),
        }
        w = V5_IC_BLEND.get(h, 0)
        if w:
            composite += mean_ic * w
            blend_w += w

    blended = round(composite / blend_w, 4) if blend_w else None
    suggestion = "hold"
    if blended is not None:
        if blended < 0.02:
            suggestion = "review_downweight"
        elif blended > 0.05:
            suggestion = "consider_upweight"

    return {
        "dimension": dimension,
        "horizons": horizon_stats,
        "blended_ic": blended,
        "blend_weights": V5_IC_BLEND,
        "suggestion": suggestion,
    }
