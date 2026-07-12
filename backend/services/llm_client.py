"""
统一 LLM 客户端 — OpenAI 兼容接口，默认 DeepSeek V4 Pro
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from config import (
    AI_MODEL,
    AI_PROVIDER,
    CLAUDE_API_KEY,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
)


def is_llm_available() -> bool:
    """是否已配置可用的 LLM API Key"""
    if AI_PROVIDER == "deepseek":
        return bool(DEEPSEEK_API_KEY)
    if AI_PROVIDER == "openai":
        return bool(OPENAI_API_KEY)
    if AI_PROVIDER == "claude":
        return bool(CLAUDE_API_KEY)
    return bool(DEEPSEEK_API_KEY or OPENAI_API_KEY or CLAUDE_API_KEY)


def chat_completion(
    user_prompt: str,
    *,
    system_prompt: str = "你是一位专业的股票基本面分析师。请用中文回答。",
    max_tokens: int = 1500,
    temperature: float = 0.3,
    max_retries: int = 0,
    model: str | None = None,
    json_mode: bool = False,
    cache_system: bool = False,
) -> str:
    """调用 LLM，返回文本内容。max_retries>0 时对超时/429/5xx 指数退避重试。"""
    if not is_llm_available():
        raise RuntimeError("未配置 LLM API Key")

    last_err: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return _chat_completion_once(
                user_prompt,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                model=model,
                json_mode=json_mode,
                cache_system=cache_system,
            )
        except Exception as exc:
            last_err = exc
            if attempt >= max_retries or not _is_retryable(exc):
                raise
            delay = _retry_delay_sec(attempt)
            time.sleep(delay)
    if last_err:
        raise last_err
    raise RuntimeError("LLM 调用失败")


def _retry_delay_sec(attempt: int) -> float:
    import config

    backoff = getattr(config, "DEBATE_RETRY_BACKOFF_SEC", [1, 3])
    if attempt < len(backoff):
        return float(backoff[attempt])
    return float(backoff[-1]) if backoff else 3.0


def _is_rate_limit(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg or "too many" in msg


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError, httpx.NetworkError)):
        return True
    msg = str(exc).lower()
    if any(k in msg for k in ("timeout", "timed out", "handshake", "connection reset", "429")):
        return True
    if "http 5" in msg or "http 429" in msg:
        return True
    return False


def _chat_completion_once(
    user_prompt: str,
    *,
    system_prompt: str,
    max_tokens: int,
    temperature: float,
    model: str | None = None,
    json_mode: bool = False,
    cache_system: bool = False,
) -> str:
    provider = _resolve_provider()
    resolved_model = model or AI_MODEL
    if provider == "deepseek":
        return _chat_openai_compatible(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
            model=resolved_model if "deepseek" in resolved_model else "deepseek-v4-pro",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=json_mode,
            cache_system=cache_system,
        )
    if provider == "openai":
        return _chat_openai_compatible(
            api_key=OPENAI_API_KEY,
            base_url=OPENAI_BASE_URL,
            model=resolved_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=json_mode,
            cache_system=cache_system,
        )
    return _chat_claude(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=max_tokens,
        temperature=temperature,
    )


def parse_json_from_response(text: str) -> Any:
    """从 LLM 回复中提取 JSON（支持 ```json 代码块）"""
    raw = text.strip()
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL)
    if match:
        raw = match.group(1).strip()
    return json.loads(raw)


def _resolve_provider() -> str:
    if AI_PROVIDER in ("deepseek", "openai", "claude"):
        return AI_PROVIDER
    if DEEPSEEK_API_KEY:
        return "deepseek"
    if OPENAI_API_KEY:
        return "openai"
    if CLAUDE_API_KEY:
        return "claude"
    return "deepseek"


def _chat_openai_compatible(
    *,
    api_key: str,
    base_url: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    temperature: float,
    json_mode: bool = False,
    cache_system: bool = False,
) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    system_msg: dict[str, Any] = {"role": "system", "content": system_prompt}
    if cache_system:
        system_msg["cache_control"] = {"type": "ephemeral"}
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            system_msg,
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    # DeepSeek V4 默认开启 thinking，会占满 token 导致 content 为空
    if "deepseek" in model.lower() or "deepseek" in base_url.lower():
        payload["thinking"] = {"type": "disabled"}

    resp = httpx.post(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=120.0,
    )
    if resp.status_code >= 400:
        detail = resp.text[:500]
        raise RuntimeError(f"LLM 请求失败 HTTP {resp.status_code}: {detail}")

    data = resp.json()
    message = data["choices"][0]["message"]
    content = (message.get("content") or "").strip()
    if not content and message.get("reasoning_content"):
        # 兜底：thinking 未关闭时尝试从 reasoning 末尾提取 JSON
        content = message["reasoning_content"].strip()
    if not content:
        raise RuntimeError("LLM 返回空内容")
    return content


def _chat_claude(
    *,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    temperature: float,
) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
    message = client.messages.create(
        model=AI_MODEL or "claude-sonnet-4-20250514",
        max_tokens=max_tokens,
        temperature=temperature,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return message.content[0].text
