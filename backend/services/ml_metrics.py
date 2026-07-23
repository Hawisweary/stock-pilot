"""ML OOS 评估指标（无 scipy 依赖的 RankIC 实现）。"""
from __future__ import annotations

import math
from typing import Sequence


def _rank_values(vals: Sequence[float]) -> list[float]:
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    return ranks


def pearson_ic(preds: Sequence[float], labels: Sequence[float]) -> float | None:
    n = len(preds)
    if n < 3 or n != len(labels):
        return None
    mp = sum(preds) / n
    ml = sum(labels) / n
    num = sum((preds[i] - mp) * (labels[i] - ml) for i in range(n))
    dp = math.sqrt(sum((p - mp) ** 2 for p in preds))
    dl = math.sqrt(sum((l - ml) ** 2 for l in labels))
    if dp < 1e-15 or dl < 1e-15:
        return None
    return num / (dp * dl)


def spearman_rank_ic(preds: Sequence[float], labels: Sequence[float]) -> float | None:
    if len(preds) < 3 or len(preds) != len(labels):
        return None
    return pearson_ic(_rank_values(list(preds)), _rank_values(list(labels)))


def long_short_return(
    preds: Sequence[float],
    labels: Sequence[float],
    *,
    quantile: float = 0.2,
) -> float | None:
    n = len(preds)
    if n < 10 or n != len(labels):
        return None
    k = max(1, int(n * quantile))
    order = sorted(range(n), key=lambda i: preds[i], reverse=True)
    top = [labels[i] for i in order[:k]]
    bot = [labels[i] for i in order[-k:]]
    return sum(top) / len(top) - sum(bot) / len(bot)


def rmse(preds: Sequence[float], labels: Sequence[float]) -> float | None:
    if labels is None or len(labels) == 0:
        return None
    if len(preds) != len(labels):
        return None
    return math.sqrt(sum((float(p) - float(l)) ** 2 for p, l in zip(preds, labels)) / len(labels))
