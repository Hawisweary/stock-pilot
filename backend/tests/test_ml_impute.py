"""ml_impute 单元测试。"""
from services.ml_impute import ImputeTable, is_valid, winsorize


def test_impute_industry_then_global():
    t = ImputeTable()
    t.add("pe_ttm", "银行", 6.0)
    t.add("pe_ttm", "银行", 8.0)
    t.add("pe_ttm", "医药", 30.0)
    t.finalize()
    assert t.lookup("pe_ttm", "银行") == 7.0
    assert t.lookup("pe_ttm", "未知") == 8.0


def test_winsorize_and_valid():
    assert is_valid(1.5)
    assert not is_valid(None)
    assert winsorize(200.0, 0.0, 120.0) == 120.0
    assert winsorize(-10.0, 0.0, 120.0) == 0.0
