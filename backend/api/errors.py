"""统一错误响应 helper — SEC-OPS P0-5"""
from __future__ import annotations

from fastapi import HTTPException


def api_error(status: int, code: str, message: str, **extra) -> None:
    """抛出带结构化 body 的 HTTPException。

    body: {"error": code, "message": message, ...extra}
    """
    raise HTTPException(status_code=status, detail={"error": code, "message": message, **extra})


def not_found(message: str = "资源不存在") -> None:
    api_error(404, "NOT_FOUND", message)


def bad_request(message: str) -> None:
    api_error(400, "BAD_REQUEST", message)


def service_unavailable(message: str = "功能未启用") -> None:
    api_error(503, "SERVICE_UNAVAILABLE", message)


def internal_error(message: str = "服务内部错误") -> None:
    api_error(500, "INTERNAL_ERROR", message)
