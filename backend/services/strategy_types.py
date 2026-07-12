"""策略选股共用类型。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SelectedStock:
    stock_id: int
    code: str
    name: str
    score: float

    def to_dict(self) -> dict:
        return {
            "stock_id": self.stock_id,
            "code": self.code,
            "name": self.name,
            "score": self.score,
        }
