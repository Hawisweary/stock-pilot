"""batch-fill 与 scheduler sync 互斥守卫"""
from __future__ import annotations

import threading
from contextlib import contextmanager

_guard_lock = threading.Lock()
_batch_fill_active = False


def _batch_fill_in_progress() -> bool:
    with _guard_lock:
        if _batch_fill_active:
            return True
    try:
        from services.job_queue import find_active_batch_fill

        return find_active_batch_fill() is not None
    except Exception:
        return False


def can_run_sync() -> tuple[bool, str]:
    """scheduler / 其他 sync 路径调用；batch-fill 执行中则跳过。"""
    if _batch_fill_in_progress():
        return False, "skipped: batch-fill in progress"
    return True, "ok"


@contextmanager
def batch_fill_session():
    """batch-fill 执行期间置位，防止 scheduler 并发 sync。"""
    global _batch_fill_active
    with _guard_lock:
        _batch_fill_active = True
    try:
        yield
    finally:
        with _guard_lock:
            _batch_fill_active = False
