"""简单 API 缓存 — 内存 LRU，5分钟 TTL"""
import time, functools, json

_cache = {}  # {key: (data, expiry)}


def cache_response(ttl_seconds: int = 300):
    """装饰器：缓存 GET 响应 ttl_seconds 秒"""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            key = f"{func.__name__}:{json.dumps(kwargs, sort_keys=True, default=str)}"
            now = time.time()
            if key in _cache:
                data, expiry = _cache[key]
                if now < expiry:
                    return data
            result = await func(*args, **kwargs)
            _cache[key] = (result, now + ttl_seconds)
            # 清理过期（最多保留100项）
            if len(_cache) > 100:
                expired = [k for k, (_, exp) in _cache.items() if now > exp]
                for k in expired: del _cache[k]
            return result
        return wrapper
    return decorator


def clear_cache():
    _cache.clear()
    return {"cleared": len(_cache)}
