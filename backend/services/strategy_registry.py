"""统一策略注册表 — 回测 / 模拟盘 / API 列表共用。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from services.v5_scorer import V5_LABELS
from services.v5_score_query import STRATEGY_ALIASES, resolve_score_spec

StrategyKind = Literal["v5", "momentum", "factor", "combo", "turtle", "sector"]


@dataclass(frozen=True)
class StrategyMeta:
    id: str
    label: str
    kind: StrategyKind
    portfolio_ready: bool = True
    default_top_n: int = 5
    default_rebalance: str = "weekly"
    default_min_score: float = 50.0
    requires_combination_id: bool = False
    factor_id: str | None = None


def _v5_meta(dim: str, label: str | None = None) -> StrategyMeta:
    return StrategyMeta(
        id=dim,
        label=label or V5_LABELS.get(dim, dim),
        kind="v5",
        portfolio_ready=True,
        default_top_n=5 if dim == "composite" else 8,
        default_rebalance="weekly" if dim in ("composite", "fundamental", "quality") else "monthly",
        default_min_score=50.0,
    )


STRATEGIES: dict[str, StrategyMeta] = {
    "composite": _v5_meta("composite", "V5 综合"),
    "fundamental": _v5_meta("fundamental"),
    "quality": _v5_meta("quality", "质量因子"),
    "industry": _v5_meta("industry", "行业景气"),
    "capital": _v5_meta("capital", "资金面"),
    "valuation": _v5_meta("valuation", "估值面"),
    "technical": _v5_meta("technical", "技术面"),
    "market_env": _v5_meta("market_env", "大盘环境"),
    "policy": _v5_meta("policy", "政策面"),
    "news": _v5_meta("news", "新闻面"),
    "mood": _v5_meta("mood", "情绪面"),
    "momentum": StrategyMeta(
        id="momentum",
        label="动量因子",
        kind="momentum",
        portfolio_ready=True,
        default_top_n=5,
        default_rebalance="weekly",
        default_min_score=50.0,
    ),
    "dual_ma": StrategyMeta(
        id="dual_ma",
        label="双均线",
        kind="factor",
        portfolio_ready=True,
        default_top_n=10,
        default_rebalance="weekly",
        default_min_score=0.0,
        factor_id="F013",
    ),
    "factor_combination": StrategyMeta(
        id="factor_combination",
        label="合成因子方案",
        kind="combo",
        portfolio_ready=True,
        default_top_n=8,
        default_rebalance="weekly",
        default_min_score=0.0,
        requires_combination_id=True,
    ),
    "index_enhance": StrategyMeta(
        id="index_enhance",
        label="指数增强",
        kind="v5",
        portfolio_ready=True,
        default_top_n=15,
        default_rebalance="monthly",
        default_min_score=50.0,
    ),
    "turtle": StrategyMeta(
        id="turtle",
        label="海龟交易",
        kind="turtle",
        portfolio_ready=True,
        default_top_n=5,
        default_rebalance="weekly",
        default_min_score=60.0,
    ),
    "sector_rotation": StrategyMeta(
        id="sector_rotation",
        label="行业轮动",
        kind="sector",
        portfolio_ready=True,
        default_top_n=10,
        default_rebalance="monthly",
        default_min_score=0.0,
    ),
}

# V5 别名并入注册表键
for alias, target in STRATEGY_ALIASES.items():
    if target in STRATEGIES and alias not in STRATEGIES:
        STRATEGIES[alias] = STRATEGIES[target]


def normalize_strategy_id(name: str) -> str:
    key = (name or "composite").strip()
    return STRATEGY_ALIASES.get(key, key)


def is_factor_id(name: str) -> bool:
    u = name.upper()
    return u.startswith("F") and u[1:].isdigit()


def get_meta(strategy: str) -> StrategyMeta | None:
    key = normalize_strategy_id(strategy)
    if key in STRATEGIES:
        return STRATEGIES[key]
    if is_factor_id(key):
        fid = key.upper()
        return StrategyMeta(
            id=fid,
            label=f"因子 {fid}",
            kind="factor",
            portfolio_ready=True,
            default_top_n=8,
            default_rebalance="weekly",
            default_min_score=0.0,
            factor_id=fid,
        )
    return None


def is_valid_strategy(strategy: str, *, combination_id: int | None = None) -> bool:
    meta = get_meta(strategy)
    if not meta or not meta.portfolio_ready:
        return False
    if meta.requires_combination_id and not combination_id:
        return False
    if meta.kind == "v5":
        v5_key = "composite" if meta.id == "index_enhance" else meta.id
        return resolve_score_spec(v5_key) is not None
    return True


def resolve_for_trading_rules(name: str) -> tuple[str, str] | None:
    """兼容 trading_rules / 旧 resolve_strategy 返回值。"""
    key = normalize_strategy_id(name)
    meta = get_meta(key)
    if not meta:
        return None
    if meta.kind == "v5":
        v5_key = "composite" if meta.id == "index_enhance" else meta.id
        spec = resolve_score_spec(v5_key)
        if not spec:
            return None
        if spec.kind == "column":
            return spec.key, spec.source
        return spec.key, f"v5_dim:{spec.source}"
    if meta.kind == "momentum":
        return "momentum", "momentum"
    if meta.kind == "factor" and meta.factor_id:
        return meta.factor_id, meta.factor_id
    if meta.kind == "combo":
        return "factor_combination", "factor_combination"
    if meta.kind == "turtle":
        return "turtle", "turtle"
    if meta.kind == "sector":
        return "sector_rotation", "sector_rotation"
    return None


def effective_v5_strategy(strategy: str) -> str:
    """index_enhance 映射到 composite 选股。"""
    key = normalize_strategy_id(strategy)
    if key == "index_enhance":
        return "composite"
    if get_meta(key) and get_meta(key).kind == "v5":
        return key
    return key


def list_strategies(*, portfolio_only: bool = False) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    order = [
        "composite",
        "fundamental",
        "quality",
        "industry",
        "capital",
        "valuation",
        "technical",
        "market_env",
        "policy",
        "news",
        "mood",
        "index_enhance",
        "momentum",
        "dual_ma",
        "turtle",
        "sector_rotation",
        "factor_combination",
    ]
    for sid in order:
        meta = STRATEGIES.get(sid)
        if not meta or sid in seen:
            continue
        if portfolio_only and not meta.portfolio_ready:
            continue
        seen.add(sid)
        out.append(
            {
                "id": meta.id,
                "label": meta.label,
                "kind": meta.kind,
                "portfolio_ready": meta.portfolio_ready,
                "default_top_n": meta.default_top_n,
                "default_rebalance": meta.default_rebalance,
                "default_min_score": meta.default_min_score,
                "requires_combination_id": meta.requires_combination_id,
                "factor_id": meta.factor_id,
            }
        )
    return out
