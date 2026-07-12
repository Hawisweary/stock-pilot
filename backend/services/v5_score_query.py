"""V5 评分查询 — 回测 / 模拟盘 / IC 共用。"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Dict, Literal

from services.v5_scorer import V5_LABELS, tier_to_pct

STRATEGY_ALIASES: dict[str, str] = {
    "valuation": "valuation",
    "val": "valuation",
    "val_score": "valuation",
    "sentiment": "news",
    "news_score": "news",
    "composite_score": "composite",
}


@dataclass(frozen=True)
class ScoreSpec:
    key: str
    kind: Literal["column", "v5_dim"]
    source: str
    label: str


_COLUMN_LABELS = {
    "composite_v5": "V5综合",
    "quality_score": "质量因子",
    "industry_score": "行业景气",
    "market_env_score": "大盘环境",
}


def _spec(key: str, kind: Literal["column", "v5_dim"], source: str) -> ScoreSpec:
    label = _COLUMN_LABELS.get(source) or V5_LABELS.get(source, source)
    return ScoreSpec(key=key, kind=kind, source=source, label=label)


SCORE_SPECS: dict[str, ScoreSpec] = {
    "composite": _spec("composite", "column", "composite_v5"),
    "composite_v5": _spec("composite_v5", "column", "composite_v5"),
    "quality": _spec("quality", "column", "quality_score"),
    "industry": _spec("industry", "column", "industry_score"),
    "market_env": _spec("market_env", "column", "market_env_score"),
}
for dim in V5_LABELS:
    if dim not in SCORE_SPECS:
        SCORE_SPECS[dim] = _spec(dim, "v5_dim", dim)


def resolve_score_spec(name: str) -> ScoreSpec | None:
    key = STRATEGY_ALIASES.get(name.strip(), name.strip())
    return SCORE_SPECS.get(key)


def resolve_strategy(name: str) -> tuple[str, str] | None:
    """兼容 trading_rules：返回 (key, column 或 v5_dim:维度)。"""
    from services.strategy_registry import resolve_for_trading_rules

    hit = resolve_for_trading_rules(name)
    if hit:
        return hit
    spec = resolve_score_spec(name)
    if not spec:
        return None
    if spec.kind == "column":
        return spec.key, spec.source
    return spec.key, f"v5_dim:{spec.source}"


def is_v5_dim_column(score_col: str | None) -> bool:
    return bool(score_col and score_col.startswith("v5_dim:"))


def v5_dim_key(score_col: str) -> str:
    return score_col.split(":", 1)[1]


def _parse_dim_score(raw: str | None, dim: str) -> float | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        ds = (parsed or {}).get("dim_scores") or {}
        if dim in ds and ds[dim] is not None:
            return float(ds[dim])
        tiers = (parsed or {}).get("tiers") or {}
        t = tiers.get(dim)
        if t is not None:
            return tier_to_pct(t)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return None


def load_score_snap_range(
    conn: sqlite3.Connection,
    spec: ScoreSpec,
    start: str,
    end: str,
) -> Dict[str, Dict[str, float]]:
    score_snap: Dict[str, Dict[str, float]] = {}
    if spec.kind == "column":
        for r in conn.execute(
            f"""SELECT s.code, cs.calc_date, cs.{spec.source} AS score
                FROM comprehensive_scores cs JOIN stocks s ON cs.stock_id = s.id
                WHERE s.is_active = 1 AND cs.{spec.source} IS NOT NULL
                  AND cs.calc_date BETWEEN ? AND ?
                ORDER BY cs.calc_date""",
            (start, end),
        ):
            score_snap.setdefault(r["code"], {})[r["calc_date"]] = float(r["score"])
        return score_snap

    for r in conn.execute(
        """SELECT s.code, cs.calc_date, cs.v5_breakdown_json
           FROM comprehensive_scores cs JOIN stocks s ON cs.stock_id = s.id
           WHERE s.is_active = 1 AND cs.v5_breakdown_json IS NOT NULL
             AND cs.calc_date BETWEEN ? AND ?
           ORDER BY cs.calc_date""",
        (start, end),
    ):
        v = _parse_dim_score(r["v5_breakdown_json"], spec.source)
        if v is not None:
            score_snap.setdefault(r["code"], {})[r["calc_date"]] = v
    return score_snap


def fetch_latest_top_n(
    conn: sqlite3.Connection,
    spec: ScoreSpec,
    min_score: float,
    top_n: int,
) -> list[sqlite3.Row | dict]:
    if spec.kind == "column":
        return list(
            conn.execute(
                f"""SELECT s.id AS stock_id, s.code, s.name, cs.{spec.source} AS score
                    FROM comprehensive_scores cs JOIN stocks s ON cs.stock_id = s.id
                    WHERE s.is_active=1 AND cs.calc_date=(SELECT MAX(calc_date) FROM comprehensive_scores)
                      AND cs.{spec.source} IS NOT NULL AND cs.{spec.source} >= ?
                    ORDER BY cs.{spec.source} DESC LIMIT ?""",
                (min_score, top_n),
            ).fetchall()
        )

    rows = conn.execute(
        """SELECT s.id AS stock_id, s.code, s.name, cs.v5_breakdown_json
           FROM comprehensive_scores cs JOIN stocks s ON cs.stock_id = s.id
           WHERE s.is_active=1 AND cs.calc_date=(SELECT MAX(calc_date) FROM comprehensive_scores)
             AND cs.v5_breakdown_json IS NOT NULL""",
    ).fetchall()
    scored: list[dict] = []
    for r in rows:
        v = _parse_dim_score(r["v5_breakdown_json"], spec.source)
        if v is not None and v >= min_score:
            scored.append(
                {
                    "stock_id": r["stock_id"],
                    "code": r["code"],
                    "name": r["name"],
                    "score": v,
                }
            )
    scored.sort(key=lambda x: float(x["score"]), reverse=True)
    return scored[:top_n]
