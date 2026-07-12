"""辩论 LLM 执行 — 单阶段 / 两阶段 / 分歧 escalate。"""
from __future__ import annotations

import json
from typing import Any

import config
from services.debate_prompt import (
    build_compact_prompt,
    build_debate_prompt,
    debate_llm_params,
    normalize_debate_json,
)


_ANALYST_KEYS = (
    "fundamental_analyst",
    "technical_analyst",
    "sentiment_analyst",
    "capital_analyst",
    "market_analyst",
)
_RISK_KEYS = ("risk_aggressive", "risk_conservative", "risk_neutral")


def debate_model(*, judge: bool = False) -> str:
    if judge and config.DEBATE_JUDGE_MODEL:
        return config.DEBATE_JUDGE_MODEL
    return config.DEBATE_MODEL or config.AI_MODEL


def needs_judge_escalation(adjusts: list[float]) -> bool:
    if len(adjusts) < 3:
        return False
    spread = max(adjusts) - min(adjusts)
    return spread >= config.DEBATE_ESCALATE_SPREAD


def _analyst_adjusts(debate: dict) -> list[float]:
    out: list[float] = []
    for key in _ANALYST_KEYS:
        block = debate.get(key) or {}
        try:
            out.append(float(block.get("score_adjust", 0)))
        except (TypeError, ValueError):
            out.append(0.0)
    return out


def _synthetic_judge(comp: dict, debate: dict) -> dict[str, Any]:
    adjusts = _analyst_adjusts(debate)
    avg = sum(adjusts) / len(adjusts) if adjusts else 0.0
    orig = float(comp.get("composite_score") or 50)
    final = max(0.0, min(100.0, round(orig + avg * 0.5, 1)))
    verdict = "持有"
    action = "持有"
    if avg > 1.5:
        verdict, action = "偏多", "持有"
    elif avg < -1.5:
        verdict, action = "偏空", "观望"
    for key in _RISK_KEYS:
        debate[key] = {
            "opinion": "分析师分歧较小，规则风控",
            "risk_level": "中",
            "key_risk": f"spread={max(adjusts)-min(adjusts):.1f}" if adjusts else "低",
        }
    debate["judge"] = {
        "verdict": verdict,
        "final_score": final,
        "confidence": 0.65,
        "risk": "中",
        "action": action,
    }
    return debate


def _call_llm(
    user_prompt: str,
    *,
    system_prompt: str,
    max_tokens: int,
    model: str | None = None,
    json_mode: bool | None = None,
) -> str:
    from services.llm_client import chat_completion

    use_json = config.DEBATE_JSON_MODE if json_mode is None else json_mode
    return chat_completion(
        user_prompt,
        system_prompt=system_prompt,
        max_tokens=max_tokens,
        temperature=0.3,
        max_retries=config.DEBATE_LLM_RETRIES,
        model=model,
        json_mode=use_json,
        cache_system=config.DEBATE_PROMPT_CACHE,
    )


def _parse_debate_text(text: str, comp: dict | None = None) -> dict | None:
    s = text.find("{")
    e = text.rfind("}") + 1
    if not (0 <= s < e):
        return None
    raw = json.loads(text[s:e])
    debate = normalize_debate_json(raw)
    if comp:
        from services.debate_align import postprocess_debate

        debate = postprocess_debate(debate, comp)
    return debate


def _build_analysts_prompt(
    stock: dict,
    comp: dict,
    news: list[dict],
    tech_signal: dict,
    macro_text: str,
) -> str:
    base = build_compact_prompt(stock, comp, news, tech_signal, macro_text)
    schema = (
        '{"fa":{"o":"","a":0,"r":"","c":0.7},"ta":{"o":"","a":0,"r":"","c":0.7},'
        '"sa":{"o":"","a":0,"r":"","c":0.7},"ca":{"o":"","a":0,"r":"","c":0.7},'
        '"ma":{"o":"","a":0,"r":"","c":0.7}}'
    )
    return base + f"\n仅输出5分析师缩写JSON: {schema}"


def _build_judge_prompt(
    stock: dict,
    comp: dict,
    analysts: dict,
) -> str:
    orig = comp.get("composite_score")
    summary = json.dumps(
        {k: analysts.get(k) for k in _ANALYST_KEYS if k in analysts},
        ensure_ascii=False,
    )[:1200]
    schema = (
        '{"ra":{"o":"","rl":"中","kr":""},"rc":{"o":"","rl":"中","kr":""},'
        '"rn":{"o":"","rl":"中","kr":""},"j":{"v":"","s":50,"c":0.7,"rk":"中","act":"持有"}}'
    )
    return (
        f"{stock['code']} {stock['name']} 原始分{orig}\n"
        f"分析师摘要: {summary}\n"
        f"输出风控+裁判缩写JSON: {schema}\n"
        f"规则: j.s与原始分{orig}差值≤10"
    )


def run_debate_llm(
    stock: dict,
    comp: dict,
    news: list[dict],
    tech_signal: dict,
    macro_text: str,
) -> dict[str, Any]:
    """执行 LLM 辩论，返回完整 debate dict（含 judge/risk）。"""
    llm_params = debate_llm_params()
    model = debate_model(judge=False)

    if config.DEBATE_TWO_PHASE:
        p1 = _build_analysts_prompt(stock, comp, news, tech_signal, macro_text)
        text1 = _call_llm(
            p1,
            system_prompt=llm_params["system_prompt"],
            max_tokens=min(450, llm_params["max_tokens"]),
            model=model,
        )
        phase1 = _parse_debate_text(text1, comp) or {}
        adjusts = _analyst_adjusts(phase1)
        if needs_judge_escalation(adjusts):
            p2 = _build_judge_prompt(stock, comp, phase1)
            judge_model = debate_model(judge=True)
            text2 = _call_llm(
                p2,
                system_prompt=llm_params["system_prompt"],
                max_tokens=min(450, llm_params["max_tokens"]),
                model=judge_model,
            )
            phase2 = normalize_debate_json(_parse_debate_text(text2, comp) or {})
            debate = {**phase1, **phase2}
            debate["_meta"] = {
                **(debate.get("_meta") or {}),
                "llm_phases": 2,
                "judge_escalated": True,
            }
        else:
            debate = _synthetic_judge(comp, phase1)
            from services.debate_align import postprocess_debate

            debate = postprocess_debate(debate, comp)
            debate["_meta"] = {
                **(debate.get("_meta") or {}),
                "llm_phases": 1,
                "judge_escalated": False,
            }
        return debate

    prompt = build_debate_prompt(stock, comp, news, tech_signal, macro_text)
    text = _call_llm(
        prompt,
        system_prompt=llm_params["system_prompt"],
        max_tokens=llm_params["max_tokens"],
        model=model,
    )
    debate = _parse_debate_text(text, comp)
    if not debate:
        raise ValueError("JSON 解析失败")
    debate["_meta"] = {**(debate.get("_meta") or {}), "llm_phases": 1}
    return debate
