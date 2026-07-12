"""熔断器与财报成功判定"""
from services.fetch_circuit import FetchCircuitBreaker, financial_step_ok
from services.fetch_planner import StockFetchPlan


def test_financial_step_ok_when_skipped():
    plan = StockFetchPlan(stock_id=1, fetch_financials=False)
    assert financial_step_ok(plan, {"errors": [{"step": "financials", "message": "x"}]}) is True


def test_financial_step_ok_on_no_errors():
    plan = StockFetchPlan(stock_id=1, fetch_financials=True)
    assert financial_step_ok(plan, {"financials_count": 0, "errors": []}) is True


def test_financial_step_ok_on_error():
    plan = StockFetchPlan(stock_id=1, fetch_financials=True)
    assert (
        financial_step_ok(
            plan,
            {"errors": [{"step": "financials", "message": "empty"}]},
        )
        is False
    )


def test_circuit_trips_after_threshold():
    cb = FetchCircuitBreaker(threshold=3)
    for _ in range(3):
        cb.record_financial(attempted=True, ok=False)
    assert cb.financials_tripped is True
    cb.record_financial(attempted=True, ok=True)
    assert cb._consecutive_financial_failures == 0
