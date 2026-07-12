"""分层辩论 — priority LLM + light 规则 fallback。"""
from __future__ import annotations

from typing import Any

import config

RISK_KEYS = ("risk_aggressive", "risk_conservative", "risk_neutral")
ANALYST_KEYS = (
    "fundamental_analyst",
    "technical_analyst",
    "sentiment_analyst",
    "capital_analyst",
    "market_analyst",
)


def compute_priority_ids(
    comprehensive: dict[int, dict],
    *,
    top_n: int | None = None,
    bottom_n: int | None = None,
) -> set[int]:
    top_n = top_n if top_n is not None else config.DEBATE_PRIORITY_TOP_N
    bottom_n = bottom_n if bottom_n is not None else config.DEBATE_PRIORITY_BOTTOM_N

    ranked: list[tuple[int, float]] = []
    for sid, comp in comprehensive.items():
        score = comp.get("composite_score")
        if score is None:
            continue
        ranked.append((sid, float(score)))
    ranked.sort(key=lambda x: x[1], reverse=True)

    ids: set[int] = set()
    if top_n > 0:
        ids.update(sid for sid, _ in ranked[:top_n])
    if bottom_n > 0 and ranked:
        ids.update(sid for sid, _ in ranked[-bottom_n:])
    return ids


def input_hash_changed(ctx, stock_id: int) -> bool:
    """相对今日已有辩论，输入 hash 是否变化（无记录视为未变化 → 走 light）。"""
    existing = ctx.existing_debate.get(stock_id)
    comp = ctx.comprehensive.get(stock_id)
    if not existing or not comp:
        return False

    from services.debate_v2 import debate_input_hash

    news_titles = [n.get("title", "") for n in ctx.news.get(stock_id, [])]
    tech = ctx.tech.get(stock_id)
    current_hash = debate_input_hash(comp, news_titles, tech)

    row_hash = existing.get("input_hash")
    if row_hash and row_hash == current_hash:
        return False

    debate_json = existing.get("debate_json")
    if debate_json:
        try:
            import json

            parsed = json.loads(debate_json) if isinstance(debate_json, str) else debate_json
            meta = parsed.get("_meta") or {}
            if meta.get("input_hash") == current_hash:
                return False
        except (json.JSONDecodeError, TypeError):
            pass
    return True


def assign_tier(
    stock_id: int,
    *,
    mode: str,
    priority_ids: set[int],
    ctx=None,
    use_llm_override: bool | None = None,
) -> tuple[str, bool]:
    """返回 (tier, use_llm)。"""
    if use_llm_override is not None:
        tier = "full_llm" if use_llm_override else "light"
        return tier, use_llm_override

    if mode in ("full", "force", "changed_only", "retry_failed", "retry"):
        tier = "retry" if mode == "retry" else "full_llm"
        return tier, True

    if mode == "tiered":
        if stock_id in priority_ids:
            return "priority", True
        if ctx is not None and input_hash_changed(ctx, stock_id):
            return "changed", True
        return "light", False

    return "full_llm", True


def _news_sentiment_adj(news: list[dict]) -> float:
    bullish = bearish = 0
    for n in news:
        label = (n.get("sentiment_label") or "").lower()
        if any(x in label for x in ("多", "positive", "bull", "利好")):
            bullish += 1
        elif any(x in label for x in ("空", "negative", "bear", "利空")):
            bearish += 1
    if bullish > bearish:
        return 1.0
    if bearish > bullish:
        return -1.0
    return 0.0


def _dimension_adj(comp: dict) -> float:
    vals = [
        comp.get(k)
        for k in (
            "fundamental_score",
            "technical_score",
            "sentiment_score",
            "capital_score",
            "policy_score",
            "mood_score",
            "val_score",
        )
        if comp.get(k) is not None
    ]
    if not vals:
        return 0.0
    avg = sum(float(v) for v in vals) / len(vals)
    if avg > 60:
        return 1.0
    if avg < 40:
        return -1.0
    return 0.0


def _fmt_dim_label(dim_key: str) -> str:
    return {
        "fundamental_score": "基本面",
        "technical_score": "技术面",
        "sentiment_score": "新闻面",
        "capital_score": "资金面",
        "mood_score": "情绪面",
    }.get(dim_key, dim_key)


def light_debate(
    stock: dict,
    comp: dict,
    news: list[dict],
    tech: dict | None,
    *,
    input_hash: str = "",
) -> dict[str, Any]:
    """无 LLM 规则版辩论 — 输出与前端兼容的完整 JSON。"""
    base = float(comp.get("composite_score") or 50)
    tech_score = float((tech or {}).get("score") or 50)
    adj = 0.0

    if tech_score > 65:
        adj += 2.0
    elif tech_score < 35:
        adj -= 2.0

    adj += _news_sentiment_adj(news)
    adj += _dimension_adj(comp)

    judge_raw = round(base + adj, 1)
    clamped = max(base - 5, min(base + 5, judge_raw))
    adjusted = round(base * 0.9 + clamped * 0.1, 1)
    adjusted = max(0.0, min(100.0, adjusted))

    signal = (tech or {}).get("signal") or "中性"
    verdict = "持有"
    action = "持有"
    if adjusted >= base + 2:
        verdict, action = "偏多", "持有"
    elif adjusted <= base - 2:
        verdict, action = "偏空", "观望"

    reason = f"规则版: tech={tech_score:.0f} 合成adj={adj:+.1f}"
    risk_block = {
        "opinion": "规则风控",
        "risk_level": "中" if abs(adj) < 3 else ("高" if adj < 0 else "低"),
        "key_risk": f"技术{signal}",
    }

    debate: dict[str, Any] = {}
    for key in ANALYST_KEYS:
        dim_key = {
            "fundamental_analyst": "fundamental_score",
            "technical_analyst": "technical_score",
            "sentiment_analyst": "sentiment_score",
            "capital_analyst": "capital_score",
            "market_analyst": "mood_score",
        }[key]
        dim_val = comp.get(dim_key)
        from services.debate_align import dimension_score_to_adjust

        per_adj = dimension_score_to_adjust(dim_val)
        if key == "technical_analyst" and tech_score < 35:
            per_adj = min(per_adj, -2.0)
        elif key == "technical_analyst" and tech_score > 65:
            per_adj = max(per_adj, 2.0)
        debate[key] = {
            "opinion": reason[:80],
            "score_adjust": per_adj,
            "key_reason": f"八维{_fmt_dim_label(dim_key)}={dim_val if dim_val is not None else '?'}",
            "confidence": 0.55,
        }
    for key in RISK_KEYS:
        debate[key] = dict(risk_block)
    debate["judge"] = {
        "verdict": verdict,
        "final_score": clamped,
        "confidence": 0.55,
        "risk": "中",
        "action": action,
    }
    debate["_meta"] = {
        "input_hash": input_hash,
        "skipped_llm": True,
        "tier": "light",
        "method": "light_rules",
    }
    return debate
