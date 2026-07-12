"""辩论 score_adjust 与八维分、文案倾向对齐 — 修正 LLM 全 0 但与利空文案矛盾。"""
from __future__ import annotations

import re
from typing import Any

_ANALYST_DIM: dict[str, str] = {
    "fundamental_analyst": "fundamental_score",
    "technical_analyst": "technical_score",
    "sentiment_analyst": "sentiment_score",
    "capital_analyst": "capital_score",
    "market_analyst": "mood_score",
}

_ANALYST_KEYS = tuple(_ANALYST_DIM.keys())

_BEARISH = re.compile(
    r"利空|偏空|空头|减持|流出|下滑|恶化|承压|弱势|悲观|风险|警戒|谨慎|不佳|疲软|下行|跌破|亏损"
)
_BULLISH = re.compile(
    r"利多|偏多|多头|增持|流入|改善|乐观|强势|向好|回暖|上行|突破|景气|复苏|亮眼|超预期"
)


def _text_tone(*parts: str) -> int:
    """-1 偏空, 0 中性, 1 偏多。"""
    text = " ".join(p for p in parts if p)
    if not text.strip():
        return 0
    b = len(_BEARISH.findall(text))
    u = len(_BULLISH.findall(text))
    if b > u and b >= 1:
        return -1
    if u > b and u >= 1:
        return 1
    if "中性" in text or "震荡" in text or "持有" in text:
        return 0
    return 0


def dimension_score_to_adjust(score: float | None) -> float:
    """八维分 0–100 → 建议调整幅度约 -5～+5。"""
    if score is None:
        return 0.0
    try:
        s = float(score)
    except (TypeError, ValueError):
        return 0.0
    return round(max(-5.0, min(5.0, (s - 50.0) / 8.0)), 1)


def reconcile_adjust(
    current: float,
    tone: int,
    dim_score: float | None,
) -> float:
    """在 LLM adjust≈0 或符号与文案/维度矛盾时给出合理 adjust。"""
    dim_adj = dimension_score_to_adjust(dim_score)

    if abs(current) >= 0.5:
        if tone < 0 and current > 0:
            return min(current, -1.5)
        if tone > 0 and current < 0:
            return max(current, 1.5)
        return round(max(-5.0, min(5.0, current)), 1)

    if tone < 0:
        if dim_adj > 0:
            return -2.0
        return min(dim_adj, -1.5) if dim_adj < 0 else -2.0
    if tone > 0:
        if dim_adj < 0:
            return 2.0
        return max(dim_adj, 1.5) if dim_adj > 0 else 2.0
    if abs(dim_adj) >= 1.0:
        return dim_adj
    return 0.0


def align_analyst_adjusts(debate: dict[str, Any], comp: dict[str, Any]) -> dict[str, Any]:
    """对齐 5 分析师 score_adjust；写入 _adjust_aligned 标记。"""
    if not debate or not comp:
        return debate

    aligned_any = False
    for role, dim_key in _ANALYST_DIM.items():
        block = debate.get(role)
        if not isinstance(block, dict):
            continue
        try:
            current = float(block.get("score_adjust") or 0)
        except (TypeError, ValueError):
            current = 0.0
        tone = _text_tone(
            str(block.get("opinion") or ""),
            str(block.get("key_reason") or ""),
        )
        dim_raw = comp.get(dim_key)
        new_adj = reconcile_adjust(current, tone, dim_raw)
        if abs(new_adj - current) >= 0.5:
            aligned_any = True
            block["score_adjust"] = new_adj
            block["_adjust_aligned"] = True
        debate[role] = block

    meta = debate.setdefault("_meta", {})
    if aligned_any:
        meta["adjust_aligned"] = True
    return debate


def align_judge_with_analysts(debate: dict[str, Any], comp: dict[str, Any]) -> dict[str, Any]:
    """分析师整体偏空/偏多时，裁判分不应与原综合分完全重合。"""
    judge = debate.get("judge")
    if not isinstance(judge, dict):
        return debate

    try:
        orig = float(comp.get("composite_score") or 50)
    except (TypeError, ValueError):
        orig = 50.0

    adjusts: list[float] = []
    for role in _ANALYST_KEYS:
        block = debate.get(role) or {}
        try:
            adjusts.append(float(block.get("score_adjust") or 0))
        except (TypeError, ValueError):
            adjusts.append(0.0)

    if not adjusts:
        return debate

    avg = sum(adjusts) / len(adjusts)
    try:
        final = float(judge.get("final_score") if judge.get("final_score") is not None else orig)
    except (TypeError, ValueError):
        final = orig

    new_final = final
    if avg <= -1.2 and final >= orig - 0.5:
        new_final = max(0.0, min(100.0, round(orig + max(-5.0, avg * 1.2), 1)))
        if judge.get("action") == "持有" and new_final < orig - 1:
            judge["action"] = "观望"
            judge["verdict"] = judge.get("verdict") or "偏空"
    elif avg >= 1.2 and final <= orig + 0.5:
        new_final = max(0.0, min(100.0, round(orig + min(5.0, avg * 1.2), 1)))

    if abs(new_final - final) >= 0.5:
        judge["final_score"] = new_final
        judge["_judge_aligned"] = True
        debate["judge"] = judge
        debate.setdefault("_meta", {})["judge_aligned"] = True

    return debate


def postprocess_debate(debate: dict[str, Any], comp: dict[str, Any]) -> dict[str, Any]:
    """normalize 之后统一后处理。"""
    debate = align_analyst_adjusts(debate, comp)
    debate = align_judge_with_analysts(debate, comp)
    return debate
