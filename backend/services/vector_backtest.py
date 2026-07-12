"""NumPy/Polars 向量化回测 — v4 加速路径"""
from config import USE_POLARS


def run_vector_backtest(days=90, top_n=5, lookback=20, pos_style="equal") -> dict:
    if USE_POLARS:
        from services.backtest_vector import run_momentum_backtest_polars

        result = run_momentum_backtest_polars(days=days, top_n=top_n, lookback=lookback)
        if result and "error" not in result:
            return result

    from services.backtest_engine import run_backtest

    return run_backtest(
        days=days,
        top_n=top_n,
        lookback=lookback,
        pos_style=pos_style,
        strategy="momentum",
        rebalance="weekly",
    )


def calc_vector_metrics(*args, **kwargs):
    """兼容旧引用"""
    from services.backtest_engine import _calc_metrics
    return _calc_metrics(*args, **kwargs)
