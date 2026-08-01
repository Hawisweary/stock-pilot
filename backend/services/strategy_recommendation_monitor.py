"""P1：L3 推荐质量监控 — regime 切换日志 + 命中率追踪。"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import date
from typing import Any, Optional

import config
from services.market_regime import get_regime_for_date, regime_bucket_label
from services.regime_validation import BACKTEST_READY_STRATEGIES, index_returns_from_kline, load_regime_rows
from services.strategy_regime_performance import (
    _build_recommendation,
    daily_returns_from_curve,
    get_strategy_regime_matrix,
    lagged_bucket_by_date,
)

DEFAULT_HORIZONS = tuple(config.REGIME_REC_OUTCOME_HORIZONS or [5, 20])


def _previous_recommendation(conn: sqlite3.Connection, before_date: str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        """SELECT trade_date, regime_bucket, primary_strategy, confidence
           FROM strategy_recommendations_daily
           WHERE trade_date < ?
           ORDER BY trade_date DESC LIMIT 1""",
        (before_date,),
    ).fetchone()
    if not row:
        return None
    return {
        "trade_date": row[0],
        "regime_bucket": row[1],
        "primary_strategy": row[2],
        "confidence": row[3],
    }


def log_regime_switch(
    conn: sqlite3.Connection,
    *,
    trade_date: str,
    new_bucket: str,
    new_strategy: str,
    confidence: Optional[float],
    prev: Optional[dict[str, Any]] = None,
    note: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """bucket 或策略变化时写入 regime_switch_log。"""
    if prev is None:
        prev = _previous_recommendation(conn, trade_date)

    prev_bucket = prev.get("regime_bucket") if prev else None
    prev_strategy = prev.get("primary_strategy") if prev else None
    bucket_changed = bool(prev_bucket and prev_bucket != new_bucket)
    strategy_changed = bool(prev_strategy and prev_strategy != new_strategy)

    if not bucket_changed and not strategy_changed:
        return None

    if not note:
        parts = []
        if bucket_changed:
            parts.append(
                f"{regime_bucket_label(prev_bucket or '')}→{regime_bucket_label(new_bucket)}"
            )
        if strategy_changed:
            parts.append(f"{prev_strategy}→{new_strategy}")
        note = " · ".join(parts)

    conn.execute(
        """INSERT INTO regime_switch_log
           (trade_date, prev_bucket, new_bucket, prev_strategy, new_strategy,
            bucket_changed, strategy_changed, confidence, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            trade_date,
            prev_bucket,
            new_bucket,
            prev_strategy,
            new_strategy,
            int(bucket_changed),
            int(strategy_changed),
            confidence,
            note,
        ),
    )
    return {
        "trade_date": trade_date,
        "prev_bucket": prev_bucket,
        "new_bucket": new_bucket,
        "prev_strategy": prev_strategy,
        "new_strategy": new_strategy,
        "bucket_changed": bucket_changed,
        "strategy_changed": strategy_changed,
        "note": note,
    }


def ensure_outcome_placeholders(
    conn: sqlite3.Connection,
    *,
    trade_date: str,
    regime_bucket: str,
    primary_strategy: str,
    confidence: Optional[float],
    matrix_as_of: Optional[str] = None,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
) -> None:
    for h in horizons:
        conn.execute(
            """INSERT OR IGNORE INTO strategy_recommendation_outcomes
               (trade_date, horizon_days, regime_bucket, primary_strategy, confidence, matrix_as_of)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (trade_date, h, regime_bucket, primary_strategy, confidence, matrix_as_of),
        )


def _load_strategy_return_cache(
    *,
    backtest_days: int = 500,
    strategies: Optional[frozenset[str]] = None,
) -> dict[str, dict[str, float]]:
    from services.backtest_engine import run_backtest

    strats = strategies or BACKTEST_READY_STRATEGIES
    cache: dict[str, dict[str, float]] = {}
    for strategy in sorted(strats):
        bt = run_backtest(days=backtest_days, strategy=strategy, rebalance="weekly")
        if bt.get("error"):
            continue
        cache[strategy] = daily_returns_from_curve(bt.get("daily_values") or [])
    return cache


def _load_benchmark_returns(days: int = 730) -> dict[str, float]:
    from services.market_index import fetch_index_kline

    kline = fetch_index_kline(
        config.REGIME_INDEX_CSI800, period="daily", days=min(days + 120, 800), with_technical=False,
    )
    return index_returns_from_kline(kline.get("kline") or [])


def compute_forward_return(daily_rets: dict[str, float], after_date: str, horizon: int) -> Optional[float]:
    dates = sorted(d for d in daily_rets if d > after_date)
    if len(dates) < horizon:
        return None
    compound = 1.0
    for d in dates[:horizon]:
        compound *= 1.0 + daily_rets[d]
    return compound - 1.0


def update_recommendation_outcomes(
    conn: sqlite3.Connection,
    *,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    backtest_days: int = 500,
    max_rows: int = 500,
) -> dict[str, Any]:
    """填充已有推荐的前瞻收益（相对 CSI800 超额）。"""
    pending = conn.execute(
        """SELECT trade_date, horizon_days, primary_strategy
           FROM strategy_recommendation_outcomes
           WHERE evaluated_at IS NULL
           ORDER BY trade_date ASC
           LIMIT ?""",
        (max_rows * len(horizons),),
    ).fetchall()
    if not pending:
        return {"updated": 0, "pending": 0}

    strategy_cache = _load_strategy_return_cache(backtest_days=backtest_days)
    benchmark = _load_benchmark_returns(days=config.REGIME_MATRIX_LOOKBACK_DAYS)
    max_h = max(horizons) if horizons else 20
    today = date.today().isoformat()

    updated = 0
    for trade_date, horizon, strategy in pending:
        strat_rets = strategy_cache.get(strategy) or {}
        strat_ret = compute_forward_return(strat_rets, trade_date, int(horizon))
        bench_ret = compute_forward_return(benchmark, trade_date, int(horizon))
        if strat_ret is None or bench_ret is None:
            continue

        dates = sorted(d for d in strat_rets if d > trade_date)
        if len(dates) < horizon:
            continue
        eval_date = dates[horizon - 1]
        if eval_date > today:
            continue

        excess = strat_ret - bench_ret
        hit = 1 if excess > 0 else 0
        conn.execute(
            """UPDATE strategy_recommendation_outcomes
               SET strategy_return_pct=?, benchmark_return_pct=?, excess_return_pct=?,
                   hit=?, evaluated_at=datetime('now')
               WHERE trade_date=? AND horizon_days=?""",
            (
                round(strat_ret * 100, 3),
                round(bench_ret * 100, 3),
                round(excess * 100, 3),
                hit,
                trade_date,
                horizon,
            ),
        )
        updated += 1

    conn.commit()
    remaining = conn.execute(
        "SELECT COUNT(*) FROM strategy_recommendation_outcomes WHERE evaluated_at IS NULL",
    ).fetchone()[0]
    return {"updated": updated, "pending": remaining}


def get_hit_rate_summary(
    conn: sqlite3.Connection,
    *,
    days: int = 365,
    horizon: int = 5,
) -> dict[str, Any]:
    rows = conn.execute(
        """SELECT trade_date, regime_bucket, primary_strategy, strategy_return_pct,
                  benchmark_return_pct, excess_return_pct, hit, confidence
           FROM strategy_recommendation_outcomes
           WHERE horizon_days=? AND evaluated_at IS NOT NULL
             AND trade_date >= date('now', ?)
           ORDER BY trade_date DESC""",
        (horizon, f"-{days} days"),
    ).fetchall()

    if not rows:
        return {
            "horizon_days": horizon,
            "window_days": days,
            "sample_count": 0,
            "hit_rate_pct": None,
            "avg_excess_return_pct": None,
            "avg_strategy_return_pct": None,
            "recent": [],
        }

    hits = [r[6] for r in rows if r[6] is not None]
    excess = [float(r[5]) for r in rows if r[5] is not None]
    strat = [float(r[3]) for r in rows if r[3] is not None]

    return {
        "horizon_days": horizon,
        "window_days": days,
        "sample_count": len(rows),
        "hit_rate_pct": round(sum(hits) / len(hits) * 100, 1) if hits else None,
        "avg_excess_return_pct": round(sum(excess) / len(excess), 3) if excess else None,
        "avg_strategy_return_pct": round(sum(strat) / len(strat), 3) if strat else None,
        "recent": [
            {
                "trade_date": r[0],
                "regime_bucket": r[1],
                "primary_strategy": r[2],
                "excess_return_pct": r[5],
                "hit": bool(r[6]),
            }
            for r in rows[:10]
        ],
    }


def get_recent_switches(conn: sqlite3.Connection, *, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT trade_date, prev_bucket, new_bucket, prev_strategy, new_strategy,
                  bucket_changed, strategy_changed, confidence, note
           FROM regime_switch_log
           ORDER BY trade_date DESC, id DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return [
        {
            "trade_date": r[0],
            "prev_bucket": r[1],
            "new_bucket": r[2],
            "prev_strategy": r[3],
            "new_strategy": r[4],
            "bucket_changed": bool(r[5]),
            "strategy_changed": bool(r[6]),
            "confidence": r[7],
            "note": r[8],
            "prev_bucket_label": regime_bucket_label(r[1] or ""),
            "new_bucket_label": regime_bucket_label(r[2] or ""),
        }
        for r in rows
    ]


def backfill_recommendation_history(
    conn: sqlite3.Connection,
    *,
    days: int = 730,
    clear_existing: bool = False,
) -> dict[str, Any]:
    """由 regime 历史 + 当前矩阵回溯 L3 推荐（用于命中率 bootstrap）。"""
    if clear_existing:
        conn.execute("DELETE FROM strategy_recommendations_daily")
        conn.execute("DELETE FROM regime_switch_log")
        conn.execute("DELETE FROM strategy_recommendation_outcomes")
        conn.commit()

    matrix = get_strategy_regime_matrix(conn, auto_refresh=False)
    cells = matrix.get("cells") or []
    matrix_as_of = matrix.get("as_of_date")
    regime_rows = load_regime_rows(conn, primary="csi800", days=days)
    if len(regime_rows) < 5:
        return {"error": "regime 样本不足", "inserted": 0}

    prev: Optional[dict[str, Any]] = None
    inserted = 0
    switches = 0

    for row in regime_rows:
        td = row["trade_date"]
        bucket = row.get("bucket") or "oscillation"
        regime = get_regime_for_date(conn, td)
        rec = _build_recommendation(cells, bucket, regime)
        primary = rec.get("primary") or {}
        strategy = primary.get("strategy")
        if not strategy:
            continue

        confidence = rec.get("confidence")
        payload = {
            "trade_date": td,
            "method": "matrix_sharpe_v1_backfill",
            "matrix_as_of": matrix_as_of,
            "market": {"regime_bucket": bucket, "regime_bucket_label": regime_bucket_label(bucket)},
            "recommendation": {
                "confidence": confidence,
                "primary": {"strategy": strategy, "label": primary.get("label"), "sharpe": primary.get("sharpe")},
                "hard_rule_match": strategy == rec.get("hard_rule_strategy"),
            },
        }
        conn.execute(
            """INSERT OR REPLACE INTO strategy_recommendations_daily
               (trade_date, regime_bucket, primary_strategy, confidence, payload_json, updated_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))""",
            (td, bucket, strategy, confidence, json.dumps(payload, ensure_ascii=False)),
        )
        sw = log_regime_switch(
            conn,
            trade_date=td,
            new_bucket=bucket,
            new_strategy=strategy,
            confidence=confidence,
            prev=prev,
        )
        if sw:
            switches += 1
        ensure_outcome_placeholders(
            conn,
            trade_date=td,
            regime_bucket=bucket,
            primary_strategy=strategy,
            confidence=confidence,
            matrix_as_of=matrix_as_of,
        )
        prev = {
            "trade_date": td,
            "regime_bucket": bucket,
            "primary_strategy": strategy,
            "confidence": confidence,
        }
        inserted += 1

    conn.commit()
    return {
        "inserted": inserted,
        "switches_logged": switches,
        "matrix_as_of": matrix_as_of,
        "start_date": regime_rows[0]["trade_date"],
        "end_date": regime_rows[-1]["trade_date"],
    }


def l3_switch_simulation_report(
    conn: sqlite3.Connection,
    regime_rows: list[dict[str, Any]],
    *,
    days: int = 365,
    backtest_days: int = 500,
) -> dict[str, Any]:
    """模拟「按 L3 矩阵推荐切换策略」的 walk-forward 收益。"""
    if len(regime_rows) < 30:
        return {"error": "regime 样本不足"}

    matrix = get_strategy_regime_matrix(conn, auto_refresh=False)
    cells = matrix.get("cells") or []
    lagged = lagged_bucket_by_date(regime_rows)

    strategy_cache = _load_strategy_return_cache(backtest_days=backtest_days)
    benchmark = _load_benchmark_returns(days=days + 120)

    sorted_dates = sorted(lagged.keys())
    if len(sorted_dates) > days:
        sorted_dates = sorted_dates[-days:]

    l3_returns: list[float] = []
    hard_returns: list[float] = []
    composite_returns: list[float] = []
    switch_days = 0
    prev_strategy: Optional[str] = None

    from services.market_regime import REGIME_BUCKET_STRATEGY_MAP

    for d in sorted_dates:
        bucket = lagged[d]
        regime_stub = {"regime_csi800_label": regime_bucket_label(bucket)}
        rec = _build_recommendation(cells, bucket, regime_stub)
        primary = rec.get("primary") or {}
        l3_strat = primary.get("strategy") or "composite"
        hard_strat = REGIME_BUCKET_STRATEGY_MAP.get(bucket, "composite")

        if prev_strategy and prev_strategy != l3_strat:
            switch_days += 1
        prev_strategy = l3_strat

        l3_r = strategy_cache.get(l3_strat, {}).get(d)
        hard_r = strategy_cache.get(hard_strat, {}).get(d)
        comp_r = strategy_cache.get("composite", {}).get(d)
        bench_r = benchmark.get(d)

        if l3_r is not None:
            l3_returns.append(l3_r)
        if hard_r is not None:
            hard_returns.append(hard_r)
        if comp_r is not None:
            composite_returns.append(comp_r)

    def _metrics(rets: list[float]) -> dict[str, Any]:
        n = len(rets)
        if n < 5:
            return {"sample_days": n, "total_return_pct": None, "sharpe": None}
        compound = 1.0
        for r in rets:
            compound *= 1.0 + r
        mean = sum(rets) / n
        var = sum((x - mean) ** 2 for x in rets) / (n - 1) if n > 1 else 0
        sd = math.sqrt(var) if var > 0 else 0
        sharpe = round(mean / sd * math.sqrt(252), 2) if sd > 0 else None
        return {
            "sample_days": n,
            "total_return_pct": round((compound - 1) * 100, 2),
            "sharpe": sharpe,
        }

    l3_m = _metrics(l3_returns)
    hard_m = _metrics(hard_returns)
    comp_m = _metrics(composite_returns)
    bench_m = _metrics([benchmark.get(d, 0) for d in sorted_dates if d in benchmark])

    lift_vs_comp = None
    if l3_m.get("sharpe") is not None and comp_m.get("sharpe") is not None:
        lift_vs_comp = round(l3_m["sharpe"] - comp_m["sharpe"], 2)

    return {
        "simulation_days": len(sorted_dates),
        "matrix_as_of": matrix.get("as_of_date"),
        "strategy_switches": switch_days,
        "l3_adaptive": l3_m,
        "hard_rule_only": hard_m,
        "static_composite": comp_m,
        "benchmark_csi800": bench_m,
        "sharpe_lift_vs_composite": lift_vs_comp,
        "verdict": (
            f"L3 自适应 Sharpe {l3_m.get('sharpe')} vs 静态综合 {comp_m.get('sharpe')} "
            f"（{switch_days} 次策略切换，矩阵 as_of={matrix.get('as_of_date')}）"
        ),
    }


def get_monitoring_dashboard(
    conn: sqlite3.Connection,
    *,
    days: int = 365,
) -> dict[str, Any]:
    h5 = get_hit_rate_summary(conn, days=days, horizon=5)
    h20 = get_hit_rate_summary(conn, days=days, horizon=20)
    return {
        "generated_at": date.today().isoformat(),
        "hit_rate_h5": h5,
        "hit_rate_h20": h20,
        "recent_switches": get_recent_switches(conn, limit=15),
        "recommendation_count": conn.execute(
            "SELECT COUNT(*) FROM strategy_recommendations_daily",
        ).fetchone()[0],
        "evaluated_outcomes": conn.execute(
            "SELECT COUNT(*) FROM strategy_recommendation_outcomes WHERE evaluated_at IS NOT NULL",
        ).fetchone()[0],
    }
