"""L3：策略推荐引擎 — L1 市场状态 + L2 绩效矩阵 → 可执行推荐。"""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Any, Optional

import config
from services.market_regime import (
    REGIME_BUCKET_STRATEGY_MAP,
    REGIME_GUIDANCE,
    get_regime_agreement_stats,
    get_regime_for_date,
    regime_bucket_label,
)
from services.strategy_registry import get_meta
from services.strategy_regime_performance import (
    JUMP_MATRIX_SOURCE,
    MIN_MATRIX_SAMPLES,
    build_matrix_recommendation,
    get_strategy_regime_matrix,
    load_matrix_from_db,
    refresh_strategy_regime_matrix,
)
from services.strategy_selector import select_top_n_dicts


def _strategy_params(strategy_id: str) -> dict[str, Any]:
    meta = get_meta(strategy_id)
    if not meta:
        return {"strategy": strategy_id, "label": strategy_id}
    return {
        "strategy": meta.id,
        "label": meta.label,
        "kind": meta.kind,
        "top_n": meta.default_top_n,
        "min_score": meta.default_min_score,
        "rebalance_schedule": meta.default_rebalance,
    }


def _top_picks(conn: sqlite3.Connection, strategy_id: str, *, top_n: int, min_score: float) -> list[dict]:
    meta = get_meta(strategy_id)
    if not meta:
        return []
    selected, err = select_top_n_dicts(
        conn=conn,
        strategy=strategy_id,
        top_n=top_n,
        min_score=min_score,
        lookback=20,
        sector_window=5,
        per_sector=2,
    )
    if err or not selected:
        return []
    return [
        {
            "code": r.get("code"),
            "name": r.get("name"),
            "score": round(float(r.get("score") or 0), 1),
            "stock_id": r.get("stock_id"),
        }
        for r in selected[:top_n]
    ]


def _find_sim_portfolio(conn: sqlite3.Connection, strategy_id: str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        """SELECT id, name, cash FROM portfolios
           WHERE default_strategy=? ORDER BY created_at DESC LIMIT 1""",
        (strategy_id,),
    ).fetchone()
    if not row:
        return None
    pid = int(row[0])
    positions = conn.execute(
        """SELECT s.code, s.name, pp.shares, pp.avg_cost
           FROM portfolio_positions pp
           JOIN stocks s ON s.id = pp.stock_id
           WHERE pp.portfolio_id=? AND pp.shares > 0
           ORDER BY pp.shares * pp.avg_cost DESC LIMIT 10""",
        (pid,),
    ).fetchall()
    return {
        "portfolio_id": pid,
        "name": row[1],
        "cash": row[2],
        "positions": [
            {"code": p[0], "name": p[1], "shares": p[2], "avg_cost": p[3]}
            for p in positions
        ],
    }


def _confidence_score(
    primary: Optional[dict],
    *,
    hard_rule_match: bool,
    bucket_agreement_pct: Optional[float],
) -> float:
    if not primary:
        return 0.0
    conf = 0.35 + min(0.35, (primary.get("sample_days") or 0) / 250)
    sh = primary.get("sharpe")
    if sh is not None:
        if sh >= 2.0:
            conf += 0.15
        elif sh >= 1.0:
            conf += 0.08
        elif sh < 0:
            conf -= 0.1
    if hard_rule_match:
        conf += 0.12
    if bucket_agreement_pct is not None and bucket_agreement_pct < 50:
        conf -= 0.08
    return round(max(0.1, min(0.98, conf)), 2)


def _rationale(
    bucket_label: str,
    primary: Optional[dict],
    hard: Optional[str],
    hard_match: bool,
) -> str:
    if not primary:
        return f"当前为「{bucket_label}」，矩阵样本不足，建议观望或维持低仓。"
    strat = primary.get("label") or primary.get("strategy")
    sh = primary.get("sharpe")
    days = primary.get("sample_days")
    parts = [f"市场处于「{bucket_label}」"]
    if sh is not None and days:
        parts.append(f"历史同类状态下 {strat} 夏普 {sh:.2f}（{days} 天样本）")
    if hard_match:
        parts.append("与硬规则映射一致")
    elif hard:
        parts.append(f"硬规则原为 {hard}，矩阵数据更优")
    return "；".join(parts) + "。"


def _enrich_pick(
    pick: Optional[dict],
    conn: sqlite3.Connection,
    *,
    with_picks: bool = False,
) -> Optional[dict[str, Any]]:
    if not pick:
        return None
    sid = str(pick.get("strategy") or "")
    params = _strategy_params(sid)
    enriched = {**pick, **params}
    if with_picks:
        try:
            enriched["top_picks"] = _top_picks(
                conn, sid, top_n=int(params.get("top_n") or 5), min_score=float(params.get("min_score") or 50),
            )
            enriched["sim_portfolio"] = _find_sim_portfolio(conn, sid)
        except Exception:
            enriched["top_picks"] = []
            enriched["sim_portfolio"] = None
    return enriched


def _matrix_cells_from_db(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
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
        }
        for r in rows
    ]


def _build_jump_opinion(
    conn: sqlite3.Connection,
    regime: dict[str, Any],
    *,
    rule_bucket: str,
    rule_primary: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """Jump Model 第二意见：L1 标签 + Jump L2 矩阵推荐（不改变主推荐）。"""
    td = regime.get("trade_date")
    if not td:
        return None

    jump_row = conn.execute(
        """SELECT regime_bucket, jump_penalty, model_version, backend
           FROM market_regime_jump_daily WHERE trade_date=? LIMIT 1""",
        (td,),
    ).fetchone()
    if not jump_row or not jump_row[0]:
        return None

    jump_bucket = str(jump_row[0])
    cells_raw = load_matrix_from_db(conn, source=JUMP_MATRIX_SOURCE)
    if not cells_raw:
        return {
            "available": True,
            "aligned": jump_bucket == rule_bucket,
            "jump_bucket": jump_bucket,
            "jump_bucket_label": regime_bucket_label(jump_bucket),
            "jump_penalty": jump_row[1],
            "model_version": jump_row[2],
            "note": "Jump L2 矩阵尚未构建，仅展示 L1 标签",
        }

    jump_cells = _matrix_cells_from_db(cells_raw)
    jump_rec = build_matrix_recommendation(
        jump_cells, jump_bucket, regime, matrix_source=JUMP_MATRIX_SOURCE,
    )
    jump_primary_raw = jump_rec.get("primary")
    rule_strat = (rule_primary or {}).get("strategy")
    jump_strat = (jump_primary_raw or {}).get("strategy")
    bucket_diverged = jump_bucket != rule_bucket
    strategy_diverged = bool(rule_strat and jump_strat and rule_strat != jump_strat)

    base = {
        "available": True,
        "jump_bucket": jump_bucket,
        "jump_bucket_label": regime_bucket_label(jump_bucket),
        "jump_penalty": jump_row[1],
        "model_version": jump_row[2],
        "matrix_as_of": cells_raw[0].get("as_of_date"),
        "bucket_diverged": bucket_diverged,
        "strategy_diverged": strategy_diverged,
    }

    if not bucket_diverged and not strategy_diverged:
        return {
            **base,
            "aligned": True,
            "note": "Jump Model 与规则 L1/L3 一致",
        }

    jump_primary = _enrich_pick(jump_primary_raw, conn, with_picks=False)
    parts = [f"Jump Model 判定今日为「{regime_bucket_label(jump_bucket)}」"]
    if jump_primary_raw:
        label = jump_primary_raw.get("label") or jump_strat
        parts.append(f"Jump L2 推荐 {label}")
        if jump_primary_raw.get("sharpe") is not None:
            parts.append(f"（夏普 {jump_primary_raw['sharpe']:.2f}）")
    if bucket_diverged:
        parts.append(f"规则 L1 为「{regime_bucket_label(rule_bucket)}」")
    parts.append("此为第二意见，主推荐仍以规则为准")

    alts = [
        a for a in (_enrich_pick(x, conn) for x in (jump_rec.get("alternatives") or []))
        if a
    ]

    return {
        **base,
        "aligned": False,
        "primary": jump_primary,
        "alternatives": alts,
        "rationale": "；".join(parts) + "。",
    }


def generate_current_recommendation(
    conn: sqlite3.Connection,
    *,
    refresh_matrix: bool = False,
    persist: bool = False,
) -> dict[str, Any]:
    """生成 L3 推荐包（L1 + L2 + 选股预览 + 模拟盘映射）。"""
    if refresh_matrix:
        refresh_strategy_regime_matrix(conn)

    matrix = get_strategy_regime_matrix(conn, auto_refresh=True)
    regime = get_regime_for_date(conn)
    rec_core = matrix.get("recommendation") or {}
    current = matrix.get("current_regime") or {}

    bucket = rec_core.get("current_bucket") or current.get("bucket") or "oscillation"
    bucket_label = rec_core.get("current_bucket_label") or regime_bucket_label(bucket)
    hard = REGIME_BUCKET_STRATEGY_MAP.get(bucket)
    primary_raw = rec_core.get("primary")
    hard_match = bool(primary_raw and hard and primary_raw.get("strategy") == hard)

    agree = get_regime_agreement_stats(conn, days=60)
    conf = _confidence_score(
        primary_raw,
        hard_rule_match=hard_match,
        bucket_agreement_pct=agree.get("bucket_agreement_pct"),
    )

    primary = _enrich_pick(primary_raw, conn, with_picks=True)
    alternatives = [_enrich_pick(a, conn) for a in (rec_core.get("alternatives") or [])]
    alternatives = [a for a in alternatives if a]
    avoid = _enrich_pick(rec_core.get("avoid"), conn)

    guidance_regime = regime.get("regime_csi800") or regime.get("regime") or "oscillation"
    guidance = REGIME_GUIDANCE.get(guidance_regime) or REGIME_GUIDANCE.get("oscillation", {})

    jump_opinion = _build_jump_opinion(
        conn,
        regime,
        rule_bucket=bucket,
        rule_primary=primary_raw,
    )

    payload = {
        "generated_at": date.today().isoformat(),
        "trade_date": regime.get("trade_date") or matrix.get("as_of_date"),
        "method": "matrix_sharpe_v1",
        "market": {
            "primary_index": config.REGIME_INDEX_CSI800,
            "regime_bucket": bucket,
            "regime_bucket_label": bucket_label,
            "regime_csi800": regime.get("regime_csi800"),
            "regime_csi800_label": regime.get("regime_csi800_label"),
            "regime_csi300_label": regime.get("regime_csi300_label") or regime.get("regime_label"),
            "volatility_20": regime.get("volatility_20_csi800") or regime.get("volatility_20"),
            "price_vs_ma60": regime.get("price_vs_ma60_csi800") or regime.get("price_vs_ma60"),
            "regime_label_agreement": bool(regime.get("regime_label_agreement")),
            "bucket_agreement_60d_pct": agree.get("bucket_agreement_pct"),
            "guidance": {
                **guidance,
                "regime_label": regime.get("regime_csi800_label") or bucket_label,
            },
            "dual_track_diverged": regime.get("regime_label_agreement") is False,
        },
        "recommendation": {
            "confidence": conf,
            "primary": primary,
            "alternatives": alternatives,
            "avoid": avoid,
            "hard_rule_strategy": hard,
            "hard_rule_match": hard_match,
            "rationale": _rationale(bucket_label, primary_raw, hard, hard_match),
            "jump_opinion": jump_opinion,
        },
        "matrix_as_of": matrix.get("as_of_date"),
        "matrix_summary": {
            b: matrix.get("matrix", {}).get(primary_raw.get("strategy") if primary_raw else "", {}).get(b)
            for b in [bucket]
        } if primary_raw else {},
    }

    if persist and payload.get("trade_date"):
        _persist(conn, payload)
    return payload


def _persist(conn: sqlite3.Connection, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    rec = payload.get("recommendation") or {}
    primary = rec.get("primary") or {}
    trade_date = payload.get("trade_date")
    bucket = (payload.get("market") or {}).get("regime_bucket")
    strategy = primary.get("strategy")
    confidence = rec.get("confidence")

    conn.execute(
        """INSERT OR REPLACE INTO strategy_recommendations_daily
           (trade_date, regime_bucket, primary_strategy, confidence, payload_json, updated_at)
           VALUES (?, ?, ?, ?, ?, datetime('now'))""",
        (
            trade_date,
            bucket,
            strategy,
            confidence,
            json.dumps(payload, ensure_ascii=False),
        ),
    )

    switch_event = None
    if trade_date and bucket and strategy:
        from services.strategy_recommendation_monitor import (
            ensure_outcome_placeholders,
            log_regime_switch,
        )

        switch_event = log_regime_switch(
            conn,
            trade_date=trade_date,
            new_bucket=bucket,
            new_strategy=strategy,
            confidence=confidence,
        )
        ensure_outcome_placeholders(
            conn,
            trade_date=trade_date,
            regime_bucket=bucket,
            primary_strategy=strategy,
            confidence=confidence,
            matrix_as_of=payload.get("matrix_as_of"),
        )

    conn.commit()
    return switch_event


def generate_and_persist_recommendation(conn: sqlite3.Connection) -> dict[str, Any]:
    """15:30 调度：同步 regime 后生成并落库 L3 推荐。"""
    payload = generate_current_recommendation(conn, persist=True)
    try:
        from services.strategy_recommendation_monitor import update_recommendation_outcomes

        payload["outcome_update"] = update_recommendation_outcomes(conn, max_rows=50)
    except Exception as e:
        payload["outcome_update"] = {"error": str(e)}
    return payload


def get_stored_recommendation(
    conn: sqlite3.Connection,
    trade_date: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    if trade_date:
        row = conn.execute(
            "SELECT payload_json FROM strategy_recommendations_daily WHERE trade_date=?",
            (trade_date,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT payload_json FROM strategy_recommendations_daily ORDER BY trade_date DESC LIMIT 1",
        ).fetchone()
    if not row or not row[0]:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return None


def get_current_recommendation(
    conn: sqlite3.Connection,
    *,
    prefer_stored: bool = True,
) -> dict[str, Any]:
    """API 入口：优先读当日已落库推荐，否则实时生成。"""
    today = date.today().isoformat()
    regime = get_regime_for_date(conn)
    td = regime.get("trade_date") or today
    if prefer_stored:
        stored = get_stored_recommendation(conn, td)
        if stored:
            return stored
        stored = get_stored_recommendation(conn)
        if stored and stored.get("trade_date") == td:
            return stored
    return generate_current_recommendation(conn, persist=False)
