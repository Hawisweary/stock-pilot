import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from services.factor_engine import FactorEngine


class _FakeConn:
    pass


def test_pct_rank_middle():
    engine = FactorEngine(_FakeConn())
    assert engine._pct(5, [1, 3, 5, 7, 9]) == 60.0


def test_pct_inv_lower_is_better():
    engine = FactorEngine(_FakeConn())
    # 最小值在样本中排名最低 -> 反向百分位最高
    assert engine._pct_inv(5, [10, 20, 30]) == 100.0


def test_yoy_growth():
    engine = FactorEngine(_FakeConn())
    assert abs(engine._yoy_growth([110, 100]) - 0.1) < 1e-6
