"""从 K 线 OHLC 序列计算与日期对齐的技术指标（与图表同源）"""
from __future__ import annotations

from typing import Any

import numpy as np


def _safe_num(v) -> float | None:
    try:
        f = float(v)
        if f != f:
            return None
        return round(f, 4)
    except (TypeError, ValueError):
        return None


def compute_technical_from_bars(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """bars: [{date, open, high, low, close, volume?}, ...]"""
    if len(bars) < 5:
        return []

    opens = np.array([float(b.get("open") or 0) for b in bars], dtype=float)
    highs = np.array([float(b.get("high") or 0) for b in bars], dtype=float)
    lows = np.array([float(b.get("low") or 0) for b in bars], dtype=float)
    closes = np.array([float(b.get("close") or 0) for b in bars], dtype=float)
    dates = [str(b.get("date", ""))[:10] for b in bars]

    from services.MyTT import ATR, BOLL, KDJ, MACD, RSI

    macd_dif, macd_dea, macd_bar = MACD(closes)
    k, d, j = KDJ(closes, highs, lows)
    rsi = RSI(closes, 14)
    upper, mid, lower = BOLL(closes)
    atr = ATR(closes, highs, lows, 14)

    technical: list[dict[str, Any]] = []
    for i in range(len(dates)):
        technical.append({
            "date": dates[i],
            "macd_dif": _safe_num(macd_dif[i]),
            "macd_dea": _safe_num(macd_dea[i]),
            "macd_bar": _safe_num(macd_bar[i]),
            "kdj_k": _safe_num(k[i]),
            "kdj_d": _safe_num(d[i]),
            "kdj_j": _safe_num(j[i]),
            "rsi14": _safe_num(rsi[i]),
            "boll_upper": _safe_num(upper[i]),
            "boll_mid": _safe_num(mid[i]),
            "boll_lower": _safe_num(lower[i]),
            "atr14": _safe_num(atr[i]),
        })
    return technical
