import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from services.industry_normalize import normalize_industry, INDUSTRY_ALIASES


def test_normalize_sw_l1_passthrough():
    assert normalize_industry("食品饮料") == "食品饮料"


def test_normalize_english_alias():
    assert normalize_industry("Consumer Staples") == "食品饮料"


def test_normalize_unknown_keeps_cleaned():
    assert normalize_industry("  自定义行业X  ") == "自定义行业X"
