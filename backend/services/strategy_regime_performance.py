"""L2：策略 × 市场状态（四格）绩效矩阵 — 回测 + 模拟盘归因。"""
from __future__ import annotations

import math
import sqlite3
from datetime import date
from typing import Any, Optional

import config
from services.market_regime import (
    REGIME_BUCKET_ORDER,
    REGIME_BUCKET_STRATEGY_MAP,
    get_regime_for_date,
    regime_bucket_label,
)
from services.regime_validation import (
    BACKTEST_READY_STRATEGIES,
    MIN_BUCKET_SAMPLES,
    load_regime_rows,
)
from services.strategy_registry import get_meta

MIN_MATRIX_SAMPLES = config.REGIME_MATRIX_MIN_SAMPLES
DEFAULT_LOOKBACK_DAYS = config.REGIME_MATRIX_LOOKBACK_DAYS
DEFAULT_BACKTEST_DAYS = config.REGIME_MATRIX_BACKTEST_DAYS
JUMP_MATRIX_SOURCE = "backtest_jump"


def _backtest_kwargs(strategy: str) -> dict[str, Any]:
    meta = get_meta(strategy)
    if not meta:
        return {"rebalance": "weekly", "top_n": 5, "min_score": 50.0}
    reb = meta.default_rebalance if meta.default_rebalance not in (None, "none") else "weekly"
    return {
        "rebalance": reb,
        "top_n": int(meta.default_top_n),
        "min_score": float(meta.default_min_score),
    }


def lagged_bucket_by_date(regime_rows: list[dict[str, Any]]) -> dict[str, str]:
    """t 日收益归因到 t-1 日 bucket（严格因果）。"""
    sorted_dates = sorted(r["trade_date"] for r in regime_rows if r.get("trade_date"))
    bucket_by_date = {r["trade_date"]: r["bucket"] for r in regime_rows if r.get("bucket")}
    return {
        sorted_dates[i]: bucket_by_date[sorted_dates[i - 1]]
        for i in range(1, len(sorted_dates))
    }


def daily_returns_from_curve(daily_values: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    prev: float | None = None
    for row in daily_values:
        d = row.get("date")
        v = float(row.get("value") or 0)
        if d and prev and prev > 0:
            out[str(d)] = v / prev - 1.0
        prev = v
    return out


def compute_cell_metrics(returns: list[float]) -> dict[str, Any]:
    n = len(returns)
    if n == 0:
        return {
            "sample_days": 0,
            "total_return_pct": None,
            "ann_return_pct": None,
            "ann_vol_pct": None,
            "sharpe": None,
            "max_drawdown_pct": None,
            "win_rate_pct": None,
            "sample_sufficient": False,
        }
    compound = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in returns:
        compound *= 1.0 + r
        if compound > peak:
            peak = compound
        dd = (peak - compound) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

    total_ret = compound - 1.0
    mean = sum(returns) / n
    var = sum((x - mean) ** 2 for x in returns) / (n - 1) if n > 1 else 0
    sd = math.sqrt(var) if var > 0 else 0
    ann_vol = sd * math.sqrt(252) if sd > 0 else 0
    ann_ret = ((1 + total_ret) ** (252 / n) - 1) if n > 0 else 0
    sharpe = (mean / sd * math.sqrt(252)) if sd > 0 else None
    win_rate = sum(1 for x in returns if x > 0) / n * 100

    return {
        "sample_days": n,
        "total_return_pct": round(total_ret * 100, 2),
        "ann_return_pct": round(ann_ret * 100, 2),
        "ann_vol_pct": round(ann_vol * 100, 2),
        "sharpe": round(sharpe, 2) if sharpe is not None else None,
        "max_drawdown_pct": round(max_dd * 100, 2),
        "win_rate_pct": round(win_rate, 1),
        "sample_sufficient": n >= MIN_MATRIX_SAMPLES,
    }


def attribute_returns_by_bucket(
    daily_returns: dict[str, float],
    lagged_buckets: dict[str, str],
) -> dict[str, list[float]]:
    grouped: dict[str, list[float]] = {b: [] for b in REGIME_BUCKET_ORDER}
    for d, ret in daily_returns.items():
        b = lagged_buckets.get(d)
        if b in grouped:
            grouped[b].append(ret)
    return grouped


def build_strategy_bucket_matrix(
    strategy: str,
    lagged_buckets: dict[str, str],
    *,
    backtest_days: int = DEFAULT_BACKTEST_DAYS,
    source: str = "backtest",
    portfolio_id: int = 0,
    daily_values: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """单策略 → 各 bucket 绩效单元。"""
    if daily_values is None:
        from services.backtest_engine import run_backtest

        bt = run_backtest(days=backtest_days, strategy=strategy, **_backtest_kwargs(strategy))
        if bt.get("error"):
            return [{"strategy": strategy, "source": source, "error": bt["error"]}]
        daily_values = bt.get("daily_values") or []

    daily_returns = daily_returns_from_curve(daily_values)
    grouped = attribute_returns_by_bucket(daily_returns, lagged_buckets)
    cells = []
    for bucket in REGIME_BUCKET_ORDER:
        metrics = compute_cell_metrics(grouped.get(bucket) or [])
        cells.append({
            "strategy": strategy,
            "bucket": bucket,
            "bucket_label": regime_bucket_label(bucket),
            "source": source,
            "portfolio_id": portfolio_id or None,
            "recommended": REGIME_BUCKET_STRATEGY_MAP.get(bucket) == strategy,
            **metrics,
        })
    return cells


def _portfolio_daily_values(conn: sqlite3.Connection, portfolio_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT snapshot_date, total_value FROM portfolio_snapshots
           WHERE portfolio_id=? ORDER BY snapshot_date""",
        (portfolio_id,),
    ).fetchall()
    return [{"date": r[0], "value": float(r[1])} for r in rows if r[1] is not None]


def refresh_strategy_regime_matrix(
    conn: sqlite3.Connection,
    *,
    primary: str = "csi800",
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    backtest_days: int = DEFAULT_BACKTEST_DAYS,
    strategies: Optional[list[str]] = None,
    include_sim_portfolios: bool = True,
) -> dict[str, Any]:
    """重算并持久化策略×状态矩阵（规则 L1）。"""
    regime_rows = load_regime_rows(conn, primary=primary, days=lookback_days)
    return refresh_strategy_regime_matrix_for_rows(
        conn,
        regime_rows,
        source="backtest",
        lookback_days=lookback_days,
        backtest_days=backtest_days,
        strategies=strategies,
        include_sim_portfolios=include_sim_portfolios,
        label_source="rules",
        primary=primary,
    )


def refresh_strategy_regime_matrix_jump(
    conn: sqlite3.Connection,
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    backtest_days: int = DEFAULT_BACKTEST_DAYS,
    strategies: Optional[list[str]] = None,
) -> dict[str, Any]:
    """重算并持久化 Jump Model L1 标签下的 L2 矩阵（source=backtest_jump）。"""
    from services.regime_validation import load_jump_regime_rows

    regime_rows = load_jump_regime_rows(conn, days=lookback_days)
    return refresh_strategy_regime_matrix_for_rows(
        conn,
        regime_rows,
        source=JUMP_MATRIX_SOURCE,
        lookback_days=lookback_days,
        backtest_days=backtest_days,
        strategies=strategies,
        include_sim_portfolios=False,
        label_source="jump",
    )


def refresh_strategy_regime_matrix_for_rows(
    conn: sqlite3.Connection,
    regime_rows: list[dict[str, Any]],
    *,
    source: str = "backtest",
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    backtest_days: int = DEFAULT_BACKTEST_DAYS,
    strategies: Optional[list[str]] = None,
    include_sim_portfolios: bool = True,
    label_source: str = "rules",
    primary: str = "csi800",
) -> dict[str, Any]:
    """通用 L2 刷新：任意 regime 标签序列 → 策略×状态矩阵。"""
    if len(regime_rows) < 30:
        return {
            "error": f"{label_source} regime 样本不足（{len(regime_rows)} 天，需 ≥30）",
            "updated_cells": 0,
            "label_source": label_source,
        }

    lagged = lagged_bucket_by_date(regime_rows)
    as_of = regime_rows[-1]["trade_date"] if regime_rows else date.today().isoformat()
    strats = strategies or sorted(BACKTEST_READY_STRATEGIES)
    all_cells: list[dict[str, Any]] = []

    for strategy in strats:
        all_cells.extend(
            build_strategy_bucket_matrix(
                strategy, lagged, backtest_days=backtest_days, source=source,
            )
        )

    if include_sim_portfolios:
        pf_rows = conn.execute(
            """SELECT id, name, default_strategy FROM portfolios
               WHERE COALESCE(default_strategy, '') != ''"""
        ).fetchall()
        for pid, name, strat in pf_rows:
            strat = (strat or "composite").strip()
            if strat not in BACKTEST_READY_STRATEGIES:
                continue
            dv = _portfolio_daily_values(conn, int(pid))
            if len(dv) < 10:
                continue
            cells = build_strategy_bucket_matrix(
                strat,
                lagged,
                source="sim",
                portfolio_id=int(pid),
                daily_values=dv,
            )
            for c in cells:
                c["portfolio_name"] = name
            all_cells.extend(cells)

    _persist_cells(conn, all_cells, as_of=as_of, lookback_days=lookback_days, backtest_days=backtest_days)
    conn.commit()
    return {
        "as_of_date": as_of,
        "primary": primary,
        "updated_cells": len(all_cells),
        "strategies": strats,
        "source": source,
        "label_source": label_source,
        "regime_days": len(regime_rows),
    }


def _rank_strategies_for_bucket(
    cells: list[dict[str, Any]],
    bucket: str,
    *,
    source: str,
) -> list[dict[str, Any]]:
    rows = [
        c for c in cells
        if (c.get("regime_bucket") == bucket or c.get("bucket") == bucket)
        and c.get("source") == source
        and c.get("sharpe") is not None
        and (c.get("sample_days") or 0) >= MIN_MATRIX_SAMPLES
    ]
    return sorted(rows, key=lambda x: (-(x.get("sharpe") or -999), -(x.get("sample_days") or 0)))


def compare_rule_vs_jump_matrix(
    conn: sqlite3.Connection,
    *,
    as_of_date: Optional[str] = None,
) -> dict[str, Any]:
    """对比规则 L2 vs Jump L2：各 bucket Top 策略、Sharpe 差、排序翻转。"""
    rule_cells = load_matrix_from_db(conn, source="backtest", as_of_date=as_of_date)
    jump_cells = load_matrix_from_db(conn, source=JUMP_MATRIX_SOURCE, as_of_date=as_of_date)
    if not rule_cells:
        return {"error": "规则 L2 矩阵为空，请先 refresh_strategy_regime_matrix"}
    if not jump_cells:
        return {"error": "Jump L2 矩阵为空，请先 rebuild_matrix_with_jump_labels"}

    as_of = as_of_date or rule_cells[0]["as_of_date"]
    bucket_comparisons: list[dict[str, Any]] = []
    cell_deltas: list[dict[str, Any]] = []
    ranking_flips = 0

    rule_by_key = {
        (r["strategy_id"], r["regime_bucket"]): r for r in rule_cells if r["source"] == "backtest"
    }
    jump_by_key = {
        (r["strategy_id"], r["regime_bucket"]): r for r in jump_cells if r["source"] == JUMP_MATRIX_SOURCE
    }

    for bucket in REGIME_BUCKET_ORDER:
        rule_ranked = _rank_strategies_for_bucket(rule_cells, bucket, source="backtest")
        jump_ranked = _rank_strategies_for_bucket(jump_cells, bucket, source=JUMP_MATRIX_SOURCE)
        rule_top = rule_ranked[0] if rule_ranked else None
        jump_top = jump_ranked[0] if jump_ranked else None
        rule_strat = rule_top.get("strategy_id") if rule_top else None
        jump_strat = jump_top.get("strategy_id") if jump_top else None
        flipped = bool(rule_strat and jump_strat and rule_strat != jump_strat)
        if flipped:
            ranking_flips += 1

        bucket_comparisons.append({
            "bucket": bucket,
            "bucket_label": regime_bucket_label(bucket),
            "hard_rule_strategy": REGIME_BUCKET_STRATEGY_MAP.get(bucket),
            "rule_top_strategy": rule_strat,
            "rule_top_sharpe": rule_top.get("sharpe") if rule_top else None,
            "rule_top_sample_days": rule_top.get("sample_days") if rule_top else None,
            "jump_top_strategy": jump_strat,
            "jump_top_sharpe": jump_top.get("sharpe") if jump_top else None,
            "jump_top_sample_days": jump_top.get("sample_days") if jump_top else None,
            "ranking_flipped": flipped,
            "jump_better_sharpe": (
                jump_top.get("sharpe") is not None
                and rule_top.get("sharpe") is not None
                and float(jump_top["sharpe"]) > float(rule_top["sharpe"])
            ) if jump_top and rule_top else None,
        })

        for strat in BACKTEST_READY_STRATEGIES:
            rk = (strat, bucket)
            rr = rule_by_key.get(rk)
            jr = jump_by_key.get(rk)
            if not rr and not jr:
                continue
            rs = rr.get("sharpe") if rr else None
            js = jr.get("sharpe") if jr else None
            cell_deltas.append({
                "strategy_id": strat,
                "bucket": bucket,
                "bucket_label": regime_bucket_label(bucket),
                "rule_sharpe": rs,
                "jump_sharpe": js,
                "sharpe_delta": round(float(js) - float(rs), 2) if rs is not None and js is not None else None,
                "rule_sample_days": rr.get("sample_days") if rr else None,
                "jump_sample_days": jr.get("sample_days") if jr else None,
            })

    jump_wins = sum(
        1 for c in bucket_comparisons
        if c.get("jump_better_sharpe") is True
    )
    significant_flips = [
        c for c in bucket_comparisons
        if c.get("ranking_flipped")
        and c.get("rule_top_sharpe") is not None
        and c.get("jump_top_sharpe") is not None
        and abs(float(c["jump_top_sharpe"]) - float(c["rule_top_sharpe"])) >= 0.15
    ]

    return {
        "as_of_date": as_of,
        "bucket_comparisons": bucket_comparisons,
        "cell_deltas": cell_deltas,
        "ranking_flips": ranking_flips,
        "jump_top_sharpe_wins": jump_wins,
        "significant_flips": significant_flips,
        "significant_flip_count": len(significant_flips),
        "recommend_l3_jump_pilot": len(significant_flips) >= 2,
        "verdict": _matrix_compare_verdict(ranking_flips, jump_wins, len(significant_flips)),
    }


def _matrix_compare_verdict(ranking_flips: int, jump_wins: int, significant: int) -> str:
    if significant >= 2:
        return (
            f"Jump L2 在 {significant} 个状态下 Top 策略与规则不同且 Sharpe 差 ≥0.15，"
            "值得在 L3 加 Jump 备选推荐（P2 候选）。"
        )
    if ranking_flips >= 2:
        return (
            f"Jump L2 有 {ranking_flips} 个 bucket 排序翻转，但 Sharpe 差异不大；"
            "建议继续累积动态 λ 标签后再评估 L3 接入。"
        )
    if jump_wins > 0:
        return "Jump L2 部分 bucket Sharpe 更优，但 Top 策略与规则基本一致。"
    return "Jump L2 与规则 L2 高度一致，暂不建议切换 L3 主基准。"


def format_matrix_compare_report_text(report: dict[str, Any]) -> str:
    if report.get("error"):
        return f"错误: {report['error']}"

    lines = [
        "📊 L2 矩阵对比：规则 L1 vs Jump Model L1",
        "━" * 56,
        f"as_of_date: {report.get('as_of_date')}",
        f"排序翻转 bucket 数: {report.get('ranking_flips')} / 4",
        f"Jump Top Sharpe 更优: {report.get('jump_top_sharpe_wins')} / 4",
        f"显著翻转 (Sharpe Δ≥0.15): {report.get('significant_flip_count')}",
        "",
        "各状态 Top 策略对比:",
    ]
    for c in report.get("bucket_comparisons") or []:
        flip = " ⚡翻转" if c.get("ranking_flipped") else ""
        lines.append(
            f"  {c.get('bucket_label'):6}  规则→ {c.get('rule_top_strategy') or '—':18} "
            f"Sharpe={c.get('rule_top_sharpe')} (n={c.get('rule_top_sample_days')})"
        )
        lines.append(
            f"          Jump→ {c.get('jump_top_strategy') or '—':18} "
            f"Sharpe={c.get('jump_top_sharpe')} (n={c.get('jump_top_sample_days')}){flip}"
        )
        hard = c.get("hard_rule_strategy")
        if hard:
            lines.append(f"          硬规则: {hard}")

    lines.extend(["", "Sharpe 差 (Jump − 规则) 最大的单元:"])
    deltas = sorted(
        [d for d in (report.get("cell_deltas") or []) if d.get("sharpe_delta") is not None],
        key=lambda x: abs(x["sharpe_delta"]),
        reverse=True,
    )[:8]
    for d in deltas:
        sign = "+" if d["sharpe_delta"] >= 0 else ""
        lines.append(
            f"  {d['strategy_id']:18} @ {d['bucket_label']:4}  "
            f"{sign}{d['sharpe_delta']:.2f}  (规则 {d['rule_sharpe']} → Jump {d['jump_sharpe']})"
        )

    lines.extend(["", report.get("verdict", "")])
    if report.get("recommend_l3_jump_pilot"):
        lines.append("→ 建议：可启动 L3 Jump 备选推荐试点（P2）。")
    return "\n".join(lines)


def _persist_cells(
    conn: sqlite3.Connection,
    cells: list[dict[str, Any]],
    *,
    as_of: str,
    lookback_days: int,
    backtest_days: int,
) -> None:
    sources = {c.get("source", "backtest") for c in cells if not c.get("error")}
    for src in sources:
        conn.execute(
            "DELETE FROM strategy_regime_metrics WHERE as_of_date=? AND source=?",
            (as_of, src),
        )
    for c in cells:
        if c.get("error"):
            continue
        conn.execute(
            """INSERT OR REPLACE INTO strategy_regime_metrics
               (strategy_id, regime_bucket, source, portfolio_id, sample_days,
                total_return_pct, ann_return_pct, ann_vol_pct, sharpe,
                max_drawdown_pct, win_rate_pct, is_recommended, as_of_date,
                lookback_days, backtest_days, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
            (
                c["strategy"],
                c["bucket"],
                c.get("source", "backtest"),
                int(c.get("portfolio_id") or 0),
                c.get("sample_days", 0),
                c.get("total_return_pct"),
                c.get("ann_return_pct"),
                c.get("ann_vol_pct"),
                c.get("sharpe"),
                c.get("max_drawdown_pct"),
                c.get("win_rate_pct"),
                1 if c.get("recommended") else 0,
                as_of,
                lookback_days,
                backtest_days,
            ),
        )


def load_matrix_from_db(
    conn: sqlite3.Connection,
    *,
    source: str = "backtest",
    as_of_date: Optional[str] = None,
) -> list[dict[str, Any]]:
    if as_of_date:
        rows = conn.execute(
            """SELECT * FROM strategy_regime_metrics
               WHERE source=? AND as_of_date=? ORDER BY strategy_id, regime_bucket""",
            (source, as_of_date),
        ).fetchall()
    else:
        latest = conn.execute(
            "SELECT MAX(as_of_date) FROM strategy_regime_metrics WHERE source=?",
            (source,),
        ).fetchone()
        if not latest or not latest[0]:
            return []
        rows = conn.execute(
            """SELECT * FROM strategy_regime_metrics
               WHERE source=? AND as_of_date=? ORDER BY strategy_id, regime_bucket""",
            (source, latest[0]),
        ).fetchall()
    cols = [d[1] for d in conn.execute("PRAGMA table_info(strategy_regime_metrics)")]
    return [dict(zip(cols, r)) for r in rows]


def _pivot_matrix(cells: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    matrix: dict[str, dict[str, Any]] = {}
    for c in cells:
        strat = c.get("strategy_id") or c.get("strategy")
        bucket = c.get("regime_bucket") or c.get("bucket")
        if not strat or not bucket:
            continue
        matrix.setdefault(strat, {})[bucket] = {
            "sample_days": c.get("sample_days"),
            "total_return_pct": c.get("total_return_pct"),
            "ann_return_pct": c.get("ann_return_pct"),
            "sharpe": c.get("sharpe"),
            "max_drawdown_pct": c.get("max_drawdown_pct"),
            "win_rate_pct": c.get("win_rate_pct"),
            "sample_sufficient": (c.get("sample_days") or 0) >= MIN_MATRIX_SAMPLES,
            "recommended": bool(c.get("is_recommended") or c.get("recommended")),
        }
    return matrix


def build_drilldown_7(
    conn: sqlite3.Connection,
    strategy: str,
    *,
    backtest_days: int = DEFAULT_BACKTEST_DAYS,
) -> list[dict[str, Any]]:
    """七格 drill-down（单策略，CSI800 七格 + 回测日收益）。"""
    from services.backtest_engine import run_backtest
    from services.market_regime import REGIME_LABELS, regime_label

    rows = load_regime_rows(conn, primary="csi800", days=DEFAULT_LOOKBACK_DAYS)
    sorted_dates = sorted(r["trade_date"] for r in rows if r.get("trade_date"))
    by_date = {r["trade_date"]: r.get("regime_csi800") or r.get("regime") for r in rows}
    regime_lagged = {
        sorted_dates[i]: by_date.get(sorted_dates[i - 1], "oscillation")
        for i in range(1, len(sorted_dates))
    }

    bt = run_backtest(days=backtest_days, strategy=strategy, rebalance="weekly")
    if bt.get("error"):
        return [{"error": bt["error"]}]
    grouped: dict[str, list[float]] = {}
    for d, ret in daily_returns_from_curve(bt.get("daily_values") or []).items():
        rg = regime_lagged.get(d)
        if rg:
            grouped.setdefault(rg, []).append(ret)

    return [
        {
            "regime_7": rg,
            "regime_7_label": REGIME_LABELS.get(rg) or regime_label(rg),
            **compute_cell_metrics(rets),
        }
        for rg, rets in sorted(grouped.items())
    ]


def get_strategy_regime_matrix(
    conn: sqlite3.Connection,
    *,
    source: str = "backtest",
    auto_refresh: bool = False,
) -> dict[str, Any]:
    """读取矩阵；空表时可触发 refresh。"""
    cells_raw = load_matrix_from_db(conn, source=source)
    if not cells_raw and auto_refresh:
        refresh_strategy_regime_matrix(conn)
        cells_raw = load_matrix_from_db(conn, source=source)

    cells = []
    for r in cells_raw:
        cells.append({
            "strategy": r["strategy_id"],
            "bucket": r["regime_bucket"],
            "bucket_label": regime_bucket_label(r["regime_bucket"]),
            "sample_days": r["sample_days"],
            "total_return_pct": r["total_return_pct"],
            "ann_return_pct": r["ann_return_pct"],
            "ann_vol_pct": r["ann_vol_pct"],
            "sharpe": r["sharpe"],
            "max_drawdown_pct": r["max_drawdown_pct"],
            "win_rate_pct": r["win_rate_pct"],
            "sample_sufficient": (r["sample_days"] or 0) >= MIN_MATRIX_SAMPLES,
            "recommended": bool(r["is_recommended"]),
            "source": r["source"],
            "portfolio_id": r["portfolio_id"] or None,
        })

    matrix = _pivot_matrix(cells_raw)
    regime = get_regime_for_date(conn)
    current_bucket = regime.get("regime_bucket_csi800") or regime.get("primary_regime_bucket")
    recommendation = _build_recommendation(cells, current_bucket, regime)

    return {
        "as_of_date": cells_raw[0]["as_of_date"] if cells_raw else None,
        "primary_index": config.REGIME_INDEX_CSI800,
        "bucket_order": list(REGIME_BUCKET_ORDER),
        "bucket_labels": {b: regime_bucket_label(b) for b in REGIME_BUCKET_ORDER},
        "current_regime": {
            "trade_date": regime.get("trade_date"),
            "bucket": current_bucket,
            "bucket_label": regime_bucket_label(str(current_bucket or "")),
            "regime_csi800": regime.get("regime_csi800"),
            "regime_csi800_label": regime.get("regime_csi800_label"),
        },
        "cells": cells,
        "matrix": matrix,
        "recommendation": recommendation,
        "hard_rule_map": REGIME_BUCKET_STRATEGY_MAP,
    }


def _build_recommendation(
    cells: list[dict[str, Any]],
    current_bucket: Optional[str],
    regime: dict[str, Any],
) -> dict[str, Any]:
    return build_matrix_recommendation(
        cells, current_bucket, regime, matrix_source="backtest",
    )


def build_matrix_recommendation(
    cells: list[dict[str, Any]],
    current_bucket: Optional[str],
    regime: dict[str, Any],
    *,
    matrix_source: str = "backtest",
) -> dict[str, Any]:
    if not current_bucket:
        current_bucket = "oscillation"

    def _bucket_backtest(bucket: str, *, sufficient_only: bool) -> list[dict[str, Any]]:
        rows = [
            c for c in cells
            if c.get("bucket") == bucket
            and c.get("source") == matrix_source
            and c.get("sharpe") is not None
        ]
        if sufficient_only:
            rows = [c for c in rows if c.get("sample_sufficient")]
        else:
            rows = [c for c in rows if (c.get("sample_days") or 0) >= MIN_MATRIX_SAMPLES]
        return rows

    bucket_cells = _bucket_backtest(current_bucket, sufficient_only=True)
    if not bucket_cells:
        bucket_cells = _bucket_backtest(current_bucket, sufficient_only=False)

    ranked = sorted(bucket_cells, key=lambda x: (-(x.get("sharpe") or -999), -(x.get("sample_days") or 0)))
    hard = REGIME_BUCKET_STRATEGY_MAP.get(current_bucket)

    primary = ranked[0] if ranked else None
    alts = ranked[1:3] if len(ranked) > 1 else []

    # 下跌市：硬规则防御策略优先（小样本 Sharpe 易失真）
    if current_bucket == "trend_down" and hard:
        hard_row = next((c for c in bucket_cells if c.get("strategy") == hard), None)
        if hard_row:
            if primary and primary.get("strategy") != hard:
                alts = [primary] + [a for a in alts if a.get("strategy") not in (hard, primary.get("strategy"))][:2]
            primary = hard_row
            alts = [a for a in alts if a.get("strategy") != hard][:2]

    avoid = ranked[-1] if len(ranked) >= 2 and ranked[-1] != primary else None

    conf = 0.0
    if primary:
        conf = min(0.95, 0.4 + (primary.get("sample_days") or 0) / 200)
        if hard and primary.get("strategy") == hard:
            conf = min(0.98, conf + 0.15)

    return {
        "current_bucket": current_bucket,
        "current_bucket_label": regime_bucket_label(current_bucket),
        "confidence": round(conf, 2),
        "primary": primary,
        "alternatives": alts,
        "avoid": avoid if avoid and avoid.get("strategy") != primary.get("strategy") else None,
        "hard_rule_strategy": hard,
        "regime_summary": regime.get("regime_csi800_label") or regime.get("primary_regime_label"),
        "matrix_source": matrix_source,
    }
