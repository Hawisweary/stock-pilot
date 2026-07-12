"""
金融数据 HTTP 客户端 — 按域名配置超时与直连（不走系统代理）
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

import requests

DEFAULT_TIMEOUT = float(os.environ.get("AFR_HTTP_TIMEOUT", "15"))
NO_PROXY_HOSTS = tuple(
    h.strip()
    for h in os.environ.get(
        "AFR_NO_PROXY_HOSTS",
        "eastmoney.com,sina.com.cn,163.com,sohu.com,gtimg.cn,10jqka.com.cn,baidu.com,cninfo.com.cn",
    ).split(",")
    if h.strip()
)

_SESSION: requests.Session | None = None


def _needs_direct(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(h in host for h in NO_PROXY_HOSTS)


def get_session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
        _SESSION.headers.setdefault(
            "User-Agent",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        )
    return _SESSION


def _direct_request(method: str, url: str, **kwargs) -> requests.Response:
    """金融域名强制直连，忽略系统 HTTP_PROXY。"""
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    kwargs.setdefault("proxies", {"http": None, "https": None})
    session = requests.Session()
    session.trust_env = False
    session.headers.update(get_session().headers)
    return session.request(method, url, **kwargs)


def get(url: str, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    if _needs_direct(url):
        return _direct_request("GET", url, **kwargs)
    return get_session().get(url, **kwargs)


def post(url: str, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    if _needs_direct(url):
        return _direct_request("POST", url, **kwargs)
    return get_session().post(url, **kwargs)
