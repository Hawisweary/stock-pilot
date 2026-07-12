"""test_auth_middleware.py — SEC-OPS P0-2 验收单测

覆盖：
  - POST/PUT/PATCH/DELETE 无 Key → 401（与 API_KEY_REQUIRED 无关）
  - 有正确 Key → 通过
  - GET 在 API_KEY_REQUIRED=false 时免鉴权
  - PUBLIC_PATHS 永远免鉴权
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import Request
from fastapi.responses import JSONResponse


def _make_request(method: str, path: str, api_key: Optional[str] = None) -> Request:
    headers = {"x-api-key": api_key} if api_key else {}
    req = MagicMock(spec=Request)
    req.method = method
    req.url = MagicMock()
    req.url.path = path
    req.headers = MagicMock()
    req.headers.get = lambda k, default=None: headers.get(k.lower(), default)
    req.query_params = {}
    return req


async def _call_next(request):
    return JSONResponse(status_code=200, content={"ok": True})


TEST_KEY = "test-secret-key-123"


@pytest.mark.asyncio
async def test_post_without_key_returns_401():
    """POST 无 Key → 401，与 API_KEY_REQUIRED=false 无关。"""
    with patch("middleware.API_KEY", TEST_KEY), patch("middleware.API_KEY_REQUIRED", False):
        from middleware import ApiKeyMiddleware
        mw = ApiKeyMiddleware(app=MagicMock())
        req = _make_request("POST", "/api/scores/recalculate")
        resp = await mw.dispatch(req, _call_next)
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_delete_without_key_returns_401():
    """DELETE 无 Key → 401。"""
    with patch("middleware.API_KEY", TEST_KEY), patch("middleware.API_KEY_REQUIRED", False):
        from middleware import ApiKeyMiddleware
        mw = ApiKeyMiddleware(app=MagicMock())
        req = _make_request("DELETE", "/api/stocks/1")
        resp = await mw.dispatch(req, _call_next)
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_post_with_correct_key_passes():
    """POST 带正确 Key → 200。"""
    with patch("middleware.API_KEY", TEST_KEY), patch("middleware.API_KEY_REQUIRED", False):
        from middleware import ApiKeyMiddleware
        mw = ApiKeyMiddleware(app=MagicMock())
        req = _make_request("POST", "/api/scores/recalculate", api_key=TEST_KEY)
        resp = await mw.dispatch(req, _call_next)
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_without_key_passes_when_not_required():
    """GET 在 API_KEY_REQUIRED=false 时免鉴权。"""
    with patch("middleware.API_KEY", TEST_KEY), patch("middleware.API_KEY_REQUIRED", False):
        from middleware import ApiKeyMiddleware
        mw = ApiKeyMiddleware(app=MagicMock())
        req = _make_request("GET", "/api/stocks")
        resp = await mw.dispatch(req, _call_next)
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_without_key_returns_401_when_required():
    """GET 在 API_KEY_REQUIRED=true 时无 Key → 401。"""
    with patch("middleware.API_KEY", TEST_KEY), patch("middleware.API_KEY_REQUIRED", True):
        from middleware import ApiKeyMiddleware
        mw = ApiKeyMiddleware(app=MagicMock())
        req = _make_request("GET", "/api/stocks")
        resp = await mw.dispatch(req, _call_next)
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_public_path_always_passes():
    """PUBLIC_PATHS 永远免鉴权，即使 API_KEY_REQUIRED=true 且无 Key。"""
    with patch("middleware.API_KEY", TEST_KEY), patch("middleware.API_KEY_REQUIRED", True):
        from middleware import ApiKeyMiddleware
        mw = ApiKeyMiddleware(app=MagicMock())
        for path in ["/api/health", "/docs", "/openapi.json"]:
            req = _make_request("GET", path)
            resp = await mw.dispatch(req, _call_next)
            assert resp.status_code == 200, f"PUBLIC_PATH {path} 应免鉴权"
