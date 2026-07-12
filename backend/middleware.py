"""
FastAPI 中间件：API Key 鉴权、请求日志
"""
from __future__ import annotations

import logging
import time
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from config import API_KEY, API_KEY_REQUIRED

logger = logging.getLogger("afr.api")

# 写操作永远需要 Key，与 API_KEY_REQUIRED 全局开关无关
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# 即使 API_KEY_REQUIRED=true + AFR_AUTH_ALL=true，这些路径也免鉴权
PUBLIC_PATHS = {"/api/health", "/api/version", "/api/scheduler/status", "/docs", "/openapi.json", "/redoc"}


def _check_key(request: Request) -> bool:
    """返回 True 表示 Key 验证通过（或不需要验证）。"""
    if not API_KEY:
        # 未配置 Key：本地开发模式，写操作记 warning 后放行
        logger.warning("AFR_API_KEY 未设置，写操作未鉴权 method=%s path=%s", request.method, request.url.path)
        return True
    key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
    return key == API_KEY


class ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if path in PUBLIC_PATHS:
            return await call_next(request)

        # 写操作：永远校验 Key，与 API_KEY_REQUIRED 无关
        if request.method in _WRITE_METHODS:
            if not _check_key(request):
                return JSONResponse(status_code=401, content={"detail": "Invalid or missing API key"})
            return await call_next(request)

        # 读操作：仅当 API_KEY_REQUIRED=true 时才要求 Key
        if API_KEY_REQUIRED and not _check_key(request):
            return JSONResponse(status_code=401, content={"detail": "Invalid or missing API key"})

        return await call_next(request)


class RequestLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
        start = time.perf_counter()
        try:
            response = await call_next(request)
            ms = int((time.perf_counter() - start) * 1000)
            logger.info(
                "request",
                extra={
                    "request_id": rid,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": ms,
                },
            )
            response.headers["X-Request-ID"] = rid
            return response
        except Exception:
            ms = int((time.perf_counter() - start) * 1000)
            logger.exception(
                "request_failed",
                extra={"request_id": rid, "method": request.method, "path": request.url.path, "duration_ms": ms},
            )
            raise
