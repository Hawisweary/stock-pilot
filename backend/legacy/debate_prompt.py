"""辩论 Prompt 构建与 JSON 规范化 — 紧凑格式 + 缩写映射。"""
from __future__ import annotations

from typing import Any

import config

_ANALYST_ALIASES = {
    "fa": "fundamental_analyst",
    "ta": "technical_analyst",
    "sa": "sentiment_analyst",
    "ca": "capital_analyst",
    "ma": "market_analyst",
    "ra": "risk_aggressive",
    "rc": "risk_conservative",
    "rn": "risk_neutral",
    "j": "judge",
}

_ANALYST_FIELDS = {
    "o": "opinion",
    "a": "score_adjust",
    "r": "key_reason",
    "c": "confidence",
}

_RISK_FIELDS = {
    "o": "opinion",
    "rl": "risk_level",
    "kr": "key_risk",
}

_JUDGE_FIELDS = {
    "v": "verdict",
    "s": "final_score",
    "c": "confidence",
    "rk": "risk",
    "act": "action",
}

_COMPACT_SCHEMA = """{"fa":{"o":"","a":0,"r":"","c":0.7},"ta":{"o":"","a":0,"r":"","c":0.7},"sa":{"o":"","a":0,"r":"","c":0.7},"ca":{"o":"","a":0,"r":"","c":0.7},"ma":{"o":"","a":0,"r":"","c":0.7},"ra":{"o":"","rl":"中","kr":""},"rc":{"o":"","rl":"中","kr":""},"rn":{"o":"","rl":"中","kr":""},"j":{"v":"","s":50,"c":0.7,"rk":"中","act":"持有"}}"""


def _fmt_score(v: Any) -> str:
    if v is None:
        return "?"
    try:
        return str(round(float(v), 1))
    except (TypeError, ValueError):
        return "?"


def _format_news_compact(news: list[dict]) -> str:
    if not news:
        return "无"
    parts = []
    for n in news[:5]:
        title = (n.get("title") or "")[:40]
        label = n.get("sentiment_label") or "中性"
        parts.append(f"{title}|{label}")
    return "; ".join(parts)


def build_compact_prompt(
    stock: dict,
    comp: dict,
    news: list[dict],
    tech_signal: dict,
    macro_text: str,
) -> str:
    scores = (
        f"S={_fmt_score(comp.get('composite_score'))} "
        f"F={_fmt_score(comp.get('fundamental_score'))} "
        f"T={_fmt_score(comp.get('technical_score'))} "
        f"N={_fmt_score(comp.get('sentiment_score'))} "
        f"C={_fmt_score(comp.get('capital_score'))} "
        f"P={_fmt_score(comp.get('policy_score'))} "
        f"M={_fmt_score(comp.get('mood_score'))} "
        f"V={_fmt_score(comp.get('val_score'))}"
    )
    tech = f"{tech_signal.get('signal', '无')}/{_fmt_score(tech_signal.get('score'))}"
    macro = macro_text or "暂无"
    news_line = _format_news_compact(news)
    orig = _fmt_score(comp.get("composite_score"))

    return (
        f"{stock['code']} {stock['name']} {stock.get('industry_sw', '')}\n"
        f"{scores}\n"
        f"tech={tech} macro={macro}\n"
        f"news: {news_line}\n"
        f"输出纯JSON，键名用缩写: {_COMPACT_SCHEMA}\n"
        f"规则: j.s为裁判分，与原始分{orig}差值≤10；偏空低于原始分，偏多高于原始分。\n"
        f"规则: 各分析师 a 为 score_adjust(-10~+10)，利空/空头/流出须 a≤-2，利多/强势须 a≥+2，中性可 0；勿全部填 0。"
    )


def build_full_prompt(
    stock: dict,
    comp: dict,
    news_text: str,
    tech_signal: dict,
    macro_text: str,
) -> str:
    return f"""分析A股：{stock['code']} {stock['name']} 行业{stock.get('industry_sw', '')}

=== 8维度评分 ===
综合:{comp['composite_score']} 基本面:{comp['fundamental_score']} 技术面:{comp['technical_score']}
新闻面:{comp['sentiment_score']} 资金面:{comp['capital_score']} 政策面:{comp['policy_score']}
情绪面:{comp['mood_score']} 估值面:{comp['val_score']}

=== 技术信号 ===
{tech_signal.get('signal', '无')} (评分{tech_signal.get('score', '?')})

=== 宏观环境 ===
{macro_text or '暂无'}

=== 近期新闻 ===
{news_text or '无近期新闻'}

请5个角色依次输出，严格JSON格式：

{{
  "fundamental_analyst": {{"opinion":"80字","score_adjust":0,"key_reason":"30字","confidence":0.7}},
  "technical_analyst": {{"opinion":"80字","score_adjust":0,"key_reason":"30字","confidence":0.7}},
  "sentiment_analyst": {{"opinion":"80字","score_adjust":0,"key_reason":"30字","confidence":0.7}},
  "capital_analyst": {{"opinion":"80字","score_adjust":0,"key_reason":"30字","confidence":0.7}},
  "market_analyst": {{"opinion":"80字","score_adjust":0,"key_reason":"30字","confidence":0.7}},
  "risk_aggressive": {{"opinion":"50字","risk_level":"中","key_risk":"40字"}},
  "risk_conservative": {{"opinion":"50字","risk_level":"中","key_risk":"40字"}},
  "risk_neutral": {{"opinion":"50字","risk_level":"中","key_risk":"40字"}},
  "judge": {{"verdict":"30字","final_score":50,"confidence":0.7,"risk":"中","action":"持有"}}
}}

裁判规则：
1. final_score 与原始分差值不得超过±10
2. 原始分: {comp['composite_score']}"""


def build_debate_prompt(
    stock: dict,
    comp: dict,
    news: list[dict],
    tech_signal: dict,
    macro_text: str,
    *,
    compact: bool | None = None,
) -> str:
    use_compact = config.DEBATE_COMPACT_PROMPT if compact is None else compact
    if use_compact:
        return build_compact_prompt(stock, comp, news, tech_signal, macro_text)
    news_text = "\n".join(f"- {n.get('title', '')}" for n in news)
    return build_full_prompt(stock, comp, news_text, tech_signal, macro_text)


def _expand_fields(raw: dict, field_map: dict[str, str]) -> dict:
    out: dict[str, Any] = {}
    for k, v in raw.items():
        key = field_map.get(k, k)
        out[key] = v
    return out


def normalize_debate_json(raw: dict) -> dict:
    """缩写 JSON → 前端完整字段名。"""
    if not raw:
        return raw

    known_full = {
        "fundamental_analyst",
        "technical_analyst",
        "sentiment_analyst",
        "capital_analyst",
        "market_analyst",
        "risk_aggressive",
        "risk_conservative",
        "risk_neutral",
        "judge",
        "_meta",
    }
    if any(k in raw for k in known_full):
        return raw

    out: dict[str, Any] = {}
    for alias, full_name in _ANALYST_ALIASES.items():
        if alias not in raw:
            continue
        block = raw[alias]
        if not isinstance(block, dict):
            out[full_name] = block
            continue
        if full_name == "judge":
            out[full_name] = _expand_fields(block, _JUDGE_FIELDS)
        elif full_name.startswith("risk_"):
            out[full_name] = _expand_fields(block, _RISK_FIELDS)
        else:
            out[full_name] = _expand_fields(block, _ANALYST_FIELDS)

    if "_meta" in raw:
        out["_meta"] = raw["_meta"]
    return out


def debate_llm_params(*, compact: bool | None = None) -> dict[str, Any]:
    use_compact = config.DEBATE_COMPACT_PROMPT if compact is None else compact
    if use_compact:
        return {
            "system_prompt": "A股投研JSON输出",
            "max_tokens": config.DEBATE_MAX_TOKENS,
            "temperature": 0.3,
        }
    return {
        "system_prompt": "你是A股投研团队，5分析师+3风控+1裁判。输出纯JSON。",
        "max_tokens": 1200,
        "temperature": 0.3,
    }
