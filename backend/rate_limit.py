"""
轻量 in-process rate limiter — 无第三方依赖。

用法：
    from rate_limit import rate_limit_check

    @router.post("/expensive")
    async def handler(request: Request):
        rate_limit_check(request, limit=10, window=60)   # 60s内最多10次
        ...

设计：
- 滑动窗口，按 (client_ip, endpoint) 分桶
- 纯内存，进程重启后重置（单实例部署足够）
- 超限返回 429 Too Many Requests
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, Request

_buckets: dict[str, deque[float]] = defaultdict(deque)
_lock = Lock()

_CLEANUP_INTERVAL = 300  # 秒
_last_cleanup = time.monotonic()


def _cleanup() -> None:
    global _last_cleanup
    now = time.monotonic()
    if now - _last_cleanup < _CLEANUP_INTERVAL:
        return
    # 删除超过最大窗口的空桶（TTL=600s）
    stale = [k for k, dq in _buckets.items() if not dq or (now - dq[-1]) > 600]
    for k in stale:
        del _buckets[k]
    _last_cleanup = now


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit_check(
    request: Request,
    *,
    limit: int = 30,
    window: int = 60,
    key_suffix: str = "",
) -> None:
    """
    滑动窗口限速。超限时抛出 HTTP 429。

    Args:
        request:    FastAPI Request 对象（用于提取 client IP）
        limit:      窗口内最大请求数
        window:     窗口长度（秒）
        key_suffix: 额外区分键（如端点路径），默认用 request.url.path
    """
    ip = _client_ip(request)
    path = key_suffix or request.url.path
    bucket_key = f"{ip}:{path}"
    now = time.monotonic()

    with _lock:
        _cleanup()
        dq = _buckets[bucket_key]
        # 移除窗口外的请求
        while dq and (now - dq[0]) > window:
            dq.popleft()
        if len(dq) >= limit:
            raise HTTPException(
                status_code=429,
                detail=f"请求过于频繁，{window}s 内最多 {limit} 次，请稍后重试",
                headers={"Retry-After": str(window)},
            )
        dq.append(now)


# 预设策略（按风险等级）
def rate_limit_write(request: Request) -> None:
    """写端点默认：60s 内 20 次"""
    rate_limit_check(request, limit=20, window=60)


def rate_limit_heavy(request: Request) -> None:
    """重计算端点：60s 内 5 次"""
    rate_limit_check(request, limit=5, window=60)


def rate_limit_onboard(request: Request) -> None:
    """批量 onboard：300s 内 3 次（防刷）"""
    rate_limit_check(request, limit=3, window=300)
