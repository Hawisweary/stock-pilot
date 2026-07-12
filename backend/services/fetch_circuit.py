"""批量抓取熔断器 — 财报连续失败时跳过剩余股票财报步骤。"""
from __future__ import annotations

import config

FINANCIAL_STEPS = frozenset({"financials", "financials_quarterly", "financials_fast"})


def financial_step_ok(plan, result: dict) -> bool:
    """按错误步骤判断财报是否成功，避免 upsert 无新行时误触发熔断。"""
    if not getattr(plan, "fetch_financials", True):
        return True
    return not any(
        e.get("step") in FINANCIAL_STEPS for e in result.get("errors", [])
    )


class FetchCircuitBreaker:
    def __init__(self, threshold: int | None = None):
        self.threshold = threshold or config.FETCH_CIRCUIT_BREAK_THRESHOLD
        self._consecutive_financial_failures = 0
        self._financials_tripped = False

    @property
    def financials_tripped(self) -> bool:
        return self._financials_tripped

    def record_financial(self, *, attempted: bool, ok: bool) -> None:
        if not attempted:
            return
        if ok:
            self._consecutive_financial_failures = 0
            return
        self._consecutive_financial_failures += 1
        if self._consecutive_financial_failures >= self.threshold:
            self._financials_tripped = True
