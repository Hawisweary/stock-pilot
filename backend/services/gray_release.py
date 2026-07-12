"""阶段Ⅱ灰度发布 — 按 client_key 哈希分流，双轨评分展示"""
from __future__ import annotations

import hashlib

from config import DUAL_SCORE_UI, GRAY_RELEASE_PCT


def _bucket(client_key: str) -> int:
    h = hashlib.md5(client_key.encode()).hexdigest()
    return int(h[:8], 16) % 100


def in_gray_bucket(client_key: str | None = None) -> bool:
    if not DUAL_SCORE_UI or GRAY_RELEASE_PCT <= 0:
        return False
    key = (client_key or "default").strip() or "default"
    return _bucket(key) < GRAY_RELEASE_PCT


def gray_status(client_key: str | None = None) -> dict:
    key = (client_key or "default").strip() or "default"
    bucket = _bucket(key)
    return {
        "dual_score_ui": DUAL_SCORE_UI,
        "gray_release_pct": GRAY_RELEASE_PCT,
        "client_key": key,
        "bucket": bucket,
        "in_gray": in_gray_bucket(key),
        "phase": "IV_full" if GRAY_RELEASE_PCT >= 100 else ("III_merge" if GRAY_RELEASE_PCT >= 50 else ("II_gray" if GRAY_RELEASE_PCT > 0 else "I_pilot")),
    }
