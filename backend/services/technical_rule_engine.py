"""技术面五档规则引擎（-2..+2）— 趋势/动量/量价，确定性公式。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

import numpy as np
import pandas as pd

from config import DB_PATH


def _tier_to_pct(tier: int) -> float:
    return float((int(tier) + 2) * 25)

ENGINE_ID = "technical_rule_v1"
MIN_BARS_FULL = 60
MIN_BARS_NEUTRAL = 20

# 可配置阈值（日后微调）
THRESHOLDS = {
    "rsi_ob": 80,
    "rsi_ob_soft": 70,
    "rsi_os": 20,
    "rsi_os_soft": 30,
    "ret5_hot": 15,
    "ret5_warm": 8,
    "vol_ratio_high": 1.5,
    "vol_ratio_low": 0.8,
    "price_chg": 2.0,
    "corr_strong": 0.6,
    "corr_mid": 0.2,
    "corr_weak": -0.2,
    "corr_bad": -0.6,
    "breakout_vol": 1.2,
    "adx_trend": 25,
}


def _clamp_tier(t: float) -> int:
    return int(max(-2, min(2, round(t))))


def _subtotal_to_tier(total: float) -> int:
    if total >= 2:
        return 2
    if total >= 0.75:
        return 1
    if total > -0.75:
        return 0
    if total >= -1.75:
        return -1
    return -2


def preprocess_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """前复权收盘价/成交量；缺失前向填充。"""
    out = df.copy()
    for col in ("open", "high", "low", "close", "volume"):
        if col not in out.columns:
            raise ValueError(f"missing column: {col}")
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.sort_index()
    out[["open", "high", "low", "close", "volume"]] = out[
        ["open", "high", "low", "close", "volume"]
    ].ffill()
    out = out.dropna(subset=["close"])
    return out


def _sma(series: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(series), np.nan)
    if len(series) < n:
        return out
    for i in range(n - 1, len(series)):
        out[i] = np.mean(series[i - n + 1 : i + 1])
    return out


def _ema(series: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(series), np.nan)
    if len(series) < n:
        return out
    k = 2 / (n + 1)
    out[n - 1] = np.mean(series[:n])
    for i in range(n, len(series)):
        out[i] = series[i] * k + out[i - 1] * (1 - k)
    return out


def _rsi_wilder(closes: np.ndarray, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes[-(period + 1) :])
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses)
    if avg_loss < 1e-12:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100 - 100 / (1 + rs))


def _macd_series(closes: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    dif = ema12 - ema26
    dea = _ema(np.nan_to_num(dif, nan=0.0), 9)
    bar = 2 * (dif - dea)
    return dif, dea, bar


def _days_since_cross(dif: np.ndarray, dea: np.ndarray, *, golden: bool) -> int | None:
    """距最近一次金叉/死叉的交易日数（0=今日刚交叉）。"""
    if len(dif) < 2:
        return None
    for i in range(len(dif) - 1, 0, -1):
        if np.isnan(dif[i]) or np.isnan(dea[i]) or np.isnan(dif[i - 1]) or np.isnan(dea[i - 1]):
            continue
        was_below = dif[i - 1] <= dea[i - 1]
        now_above = dif[i] > dea[i]
        was_above = dif[i - 1] >= dea[i - 1]
        now_below = dif[i] < dea[i]
        if golden and was_below and now_above:
            return len(dif) - 1 - i
        if not golden and was_above and now_below:
            return len(dif) - 1 - i
    return None


def _adx_di(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> tuple[float, float, float]:
    if len(close) < period + 2:
        return 20.0, 0.0, 0.0
    tr_list, pdm_list, mdm_list = [], [], []
    for i in range(1, len(close)):
        tr = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )
        up = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        pdm = up if up > down and up > 0 else 0.0
        mdm = down if down > up and down > 0 else 0.0
        tr_list.append(tr)
        pdm_list.append(pdm)
        mdm_list.append(mdm)

    def wilder_smooth(data: list[float], n: int) -> list[float]:
        if len(data) < n:
            return []
        val = sum(data[:n])
        out = [val]
        for x in data[n:]:
            val = val - val / n + x
            out.append(val)
        return out

    atr = wilder_smooth(tr_list, period)
    spdm = wilder_smooth(pdm_list, period)
    smdm = wilder_smooth(mdm_list, period)
    if not atr or atr[-1] <= 0:
        return 20.0, 0.0, 0.0
    pdi = 100 * spdm[-1] / atr[-1]
    mdi = 100 * smdm[-1] / atr[-1]
    dx = 100 * abs(pdi - mdi) / max(pdi + mdi, 1e-6)
    # 简化：用末段 DX 均值近似 ADX
    dx_vals = []
    for j in range(max(0, len(spdm) - period), len(spdm)):
        if atr[j] > 0:
            pd = 100 * spdm[j] / atr[j]
            md = 100 * smdm[j] / atr[j]
            dx_vals.append(100 * abs(pd - md) / max(pd + md, 1e-6))
    adx = float(np.mean(dx_vals[-period:])) if dx_vals else dx
    return adx, float(pdi), float(mdi)


def _ma_alignment_score(ma5: float, ma10: float, ma20: float, ma60: float) -> float:
    if ma5 > ma10 > ma20 > ma60:
        return 2.0
    if ma5 > ma10 > ma20 and ma20 <= ma60:
        return 1.0
    if ma5 < ma10 < ma20 < ma60:
        return -2.0
    if ma5 < ma10 < ma20 and ma20 >= ma60:
        return -1.0
    return 0.0


def _price_position_score(close: float, ma20: float) -> float:
    if ma20 <= 0:
        return 0.0
    ratio = close / ma20
    if ratio > 1.08:
        return 1.0
    if ratio > 1.02:
        return 0.5
    if ratio >= 0.98:
        return 0.0
    if ratio >= 0.92:
        return -0.5
    return -1.0


def _adx_score(adx: float, pdi: float, mdi: float) -> float:
    t = THRESHOLDS["adx_trend"]
    if adx > t and pdi > mdi:
        return 1.0
    if adx > t and pdi < mdi:
        return -1.0
    return 0.0


def score_trend_module(df: pd.DataFrame) -> dict[str, Any]:
    c = df["close"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    ma5 = _sma(c, 5)[-1]
    ma10 = _sma(c, 10)[-1]
    ma20 = _sma(c, 20)[-1]
    ma60 = _sma(c, 60)[-1]
    close = c[-1]
    adx, pdi, mdi = _adx_di(h, l, c)
    align = _ma_alignment_score(ma5, ma10, ma20, ma60)
    pos = _price_position_score(close, ma20)
    adx_s = _adx_score(adx, pdi, mdi)
    total = align + pos + adx_s
    return {
        "trend_score": round(total, 2),
        "trend_tier": _subtotal_to_tier(total),
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "ma60": ma60,
        "adx": round(adx, 2),
        "pdi": round(pdi, 2),
        "mdi": round(mdi, 2),
    }


def _rsi_score(rsi: float) -> float:
    t = THRESHOLDS
    if rsi > t["rsi_ob"]:
        return -1.0
    if rsi > t["rsi_ob_soft"]:
        return -0.5
    if rsi >= t["rsi_os_soft"]:
        return 0.0
    if rsi >= t["rsi_os"]:
        return 0.5
    return 1.0


def _macd_score(bar: float, days_since_golden: int | None, days_since_death: int | None) -> float:
    if bar > 0:
        if days_since_golden is not None and days_since_golden <= 10:
            return 1.0
        if days_since_death is not None and days_since_death <= 5:
            return -0.5
        return 0.0
    if bar < 0:
        if days_since_death is not None and days_since_death <= 10:
            return -1.0
        if days_since_golden is not None and days_since_golden <= 5:
            return 0.5
        return 0.0
    return 0.0


def _ret5_score(ret5_pct: float) -> float:
    if ret5_pct > THRESHOLDS["ret5_hot"]:
        return -1.0
    if ret5_pct > THRESHOLDS["ret5_warm"]:
        return -0.5
    if ret5_pct >= -THRESHOLDS["ret5_warm"]:
        return 0.0
    if ret5_pct >= -THRESHOLDS["ret5_hot"]:
        return 0.5
    return 1.0


def score_momentum_module(df: pd.DataFrame) -> dict[str, Any]:
    c = df["close"].to_numpy(dtype=float)
    dif, dea, bar = _macd_series(c)
    rsi = _rsi_wilder(c, 14)
    ret5 = (c[-1] / c[-6] - 1) * 100 if len(c) >= 6 else 0.0
    rsi_s = _rsi_score(rsi)
    macd_s = _macd_score(
        float(bar[-1]) if not np.isnan(bar[-1]) else 0.0,
        _days_since_cross(dif, dea, golden=True),
        _days_since_cross(dif, dea, golden=False),
    )
    ret_s = _ret5_score(ret5)
    total = rsi_s + macd_s + ret_s
    return {
        "momentum_score": round(total, 2),
        "momentum_tier": _subtotal_to_tier(total),
        "rsi14": round(rsi, 2),
        "macd_bar": round(float(bar[-1]), 4) if not np.isnan(bar[-1]) else 0.0,
        "ret5_pct": round(ret5, 2),
    }


def _volume_ratio_score(vol_ratio: float, chg_pct: float) -> float:
    t = THRESHOLDS
    if vol_ratio > t["vol_ratio_high"]:
        if chg_pct > t["price_chg"]:
            return 1.0
        if chg_pct < -t["price_chg"]:
            return -1.0
        return 0.0
    if vol_ratio < t["vol_ratio_low"]:
        if chg_pct > t["price_chg"]:
            return -0.5
        if chg_pct < -t["price_chg"]:
            return 0.5
        return 0.0
    return 0.0


def _corr_score(corr: float) -> float:
    t = THRESHOLDS
    if corr > t["corr_strong"]:
        return 1.0
    if corr > t["corr_mid"]:
        return 0.5
    if corr >= t["corr_weak"]:
        return 0.0
    if corr >= t["corr_bad"]:
        return -0.5
    return -1.0


def _breakout_score(close: float, high20: float, low20: float, vol_ratio: float) -> float:
    brk_vol = THRESHOLDS["breakout_vol"]
    if close >= high20:
        return 1.0 if vol_ratio > brk_vol else 0.5
    if close <= low20:
        return -1.0 if vol_ratio > brk_vol else -0.5
    return 0.0


def score_volume_module(df: pd.DataFrame) -> dict[str, Any]:
    c = df["close"].to_numpy(dtype=float)
    v = df["volume"].to_numpy(dtype=float)
    vol5 = np.mean(v[-5:]) if len(v) >= 5 else v[-1]
    vol_ratio = float(v[-1] / vol5) if vol5 > 0 else 1.0
    chg_pct = (c[-1] / c[-2] - 1) * 100 if len(c) >= 2 else 0.0
    window = min(20, len(c))
    corr = 0.0
    if window >= 5:
        corr = float(np.corrcoef(c[-window:], v[-window:])[0, 1])
        if np.isnan(corr):
            corr = 0.0
    high20 = float(np.max(c[-window:]))
    low20 = float(np.min(c[-window:]))
    vr_s = _volume_ratio_score(vol_ratio, chg_pct)
    corr_s = _corr_score(corr)
    brk_s = _breakout_score(c[-1], high20, low20, vol_ratio)
    total = vr_s + corr_s + brk_s
    return {
        "volume_score": round(total, 2),
        "volume_tier": _subtotal_to_tier(total),
        "vol_ratio": round(vol_ratio, 2),
        "vol_price_corr20": round(corr, 3),
        "chg_pct": round(chg_pct, 2),
    }


def fuse_technical_tiers(trend_tier: int, momentum_tier: int, volume_tier: int) -> int:
    raw = 0.4 * trend_tier + 0.3 * momentum_tier + 0.3 * volume_tier
    final = _clamp_tier(raw)
    tiers = [trend_tier, momentum_tier, volume_tier]
    neg2 = sum(1 for t in tiers if t <= -2)
    neg1 = sum(1 for t in tiers if t <= -1)
    if neg2 >= 2 or (neg2 >= 1 and neg1 >= 2):
        final = -2
    pos2 = sum(1 for t in tiers if t >= 2)
    pos1 = sum(1 for t in tiers if t >= 1)
    if pos2 >= 2 or (pos2 >= 1 and pos1 >= 2):
        final = 2
    return final


def compute_technical_tier(df: pd.DataFrame) -> dict[str, Any]:
    """主入口：OHLCV DataFrame → 五档技术面结果。"""
    data = preprocess_ohlcv(df)
    n = len(data)
    if n < MIN_BARS_NEUTRAL:
        return {
            "engine": ENGINE_ID,
            "final_technical_tier": 0,
            "score": _tier_to_pct(0),
            "reason": f"bars<{MIN_BARS_NEUTRAL}",
        }
    use = data.tail(max(n, MIN_BARS_FULL)) if n >= MIN_BARS_FULL else data
    trend = score_trend_module(use)
    momentum = score_momentum_module(use)
    volume = score_volume_module(use)
    final_tier = fuse_technical_tiers(
        trend["trend_tier"], momentum["momentum_tier"], volume["volume_tier"]
    )
    out = {
        "engine": ENGINE_ID,
        "bars": n,
        **trend,
        **momentum,
        **volume,
        "final_technical_tier": final_tier,
        "score": _tier_to_pct(final_tier),
        "advice": _tier_advice(final_tier),
    }
    return out


def _tier_advice(tier: int) -> str:
    tips = {
        2: "趋势与量价共振偏多，可顺势关注",
        1: "技术面温和偏多",
        0: "多空均衡，观望为主",
        -1: "技术面偏弱，注意回调风险",
        -2: "多项指标偏空，谨慎参与",
    }
    return tips.get(tier, "观望")


def ohlcv_hash(df: pd.DataFrame) -> str:
    tail = preprocess_ohlcv(df).tail(60)
    raw = "|".join(
        f"{idx}|{row.close:.4f}|{row.volume:.0f}"
        for idx, row in zip(tail.index.astype(str), tail.itertuples())
    )
    return hashlib.md5(f"{ENGINE_ID}|{raw}".encode()).hexdigest()


def persist_rule_result(stock_id: int, result: dict[str, Any], *, input_hash: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """INSERT OR REPLACE INTO tech_analysis_cache
               (stock_id, input_hash, daily_close, weekly_close, score, signal, advice, reasoning, full_result)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                stock_id,
                input_hash,
                result.get("ma20"),
                None,
                result.get("score"),
                str(result.get("final_technical_tier")),
                result.get("advice", ""),
                json.dumps(
                    {
                        "trend_tier": result.get("trend_tier"),
                        "momentum_tier": result.get("momentum_tier"),
                        "volume_tier": result.get("volume_tier"),
                    },
                    ensure_ascii=False,
                ),
                json.dumps(result, ensure_ascii=False),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def tier_from_pct_score(score: float | None) -> int | None:
    """百分制技术分 → 五档（仅用于规则引擎离散分）。"""
    if score is None:
        return None
    return _clamp_tier(float(score) / 25 - 2)
