"""辩论 score_adjust 对齐逻辑"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.debate_align import align_analyst_adjusts, align_judge_with_analysts, postprocess_debate


def test_bearish_zero_adjusts_to_negative():
    comp = {
        "composite_score": 48.2,
        "fundamental_score": 72.6,
        "technical_score": 25.0,
        "sentiment_score": 31.6,
        "capital_score": 31.6,
        "mood_score": 36.5,
    }
    debate = {
        "fundamental_analyst": {
            "opinion": "利空",
            "score_adjust": 0,
            "key_reason": "强烈利空",
            "confidence": 0.7,
        },
        "technical_analyst": {
            "opinion": "利空",
            "score_adjust": 0,
            "key_reason": "短期空头趋势明显",
            "confidence": 0.7,
        },
        "sentiment_analyst": {
            "opinion": "利空",
            "score_adjust": 0,
            "key_reason": "主力资金连续净流出",
            "confidence": 0.7,
        },
        "capital_analyst": {
            "opinion": "利空",
            "score_adjust": 0,
            "key_reason": "高管集体减持",
            "confidence": 0.7,
        },
        "market_analyst": {
            "opinion": "中性",
            "score_adjust": 0,
            "key_reason": "PMI扩张但CPI高企",
            "confidence": 0.7,
        },
        "judge": {
            "verdict": "持有",
            "final_score": 48.2,
            "confidence": 0.65,
            "risk": "中",
            "action": "持有",
        },
    }
    out = postprocess_debate(debate, comp)
    assert out["fundamental_analyst"]["score_adjust"] == -2.0
    assert out["technical_analyst"]["score_adjust"] < 0
    assert out["capital_analyst"]["score_adjust"] < 0
    assert out["judge"]["final_score"] < 48.2


def test_dimension_drives_neutral_adjust():
    comp = {"composite_score": 50, "technical_score": 72.0}
    debate = {
        "technical_analyst": {
            "opinion": "震荡",
            "score_adjust": 0,
            "key_reason": "中性整理",
            "confidence": 0.6,
        }
    }
    out = align_analyst_adjusts(debate, comp)
    assert out["technical_analyst"]["score_adjust"] > 0
