"""辩论批量共享类型。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DebateTarget:
    stock_id: int
    code: str
    tier: str
    use_llm: bool
