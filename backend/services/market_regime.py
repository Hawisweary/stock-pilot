"""市场状态分类（趋势/震荡/高波动/流动性枯竭）。

Phase A：指数 K 线 + 全市场广度（涨跌家数、成交额、行业轮动、相关性）。
Phase B：两层规则分类，输出 regime + regime_label，供 V5 动态权重与前端展示。
Phase C：仓位/风控建议 + V5 权重影响说明，供 Dashboard 与个股页展示。
"""
from __future__ import annotations

import logging
import math
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Optional

import config
from services.market_index import fetch_index_kline

logger = logging.getLogger(__name__)

REGIME_ORDER = [
    "liquidity_drought",
    "high_volatility",
    "strong_trend_up",
    "strong_trend_down",
    "weak_trend_up",
    "weak_trend_down",
    "oscillation",
]

REGIME_LABELS: dict[str, str] = {
    "strong_trend_up": "趋势上涨",
    "weak_trend_up": "趋势上涨",
    "strong_trend_down": "趋势下跌",
    "weak_trend_down": "趋势下跌",
    "oscillation": "震荡",
    "high_volatility": "高波动",
    "liquidity_drought": "流动性枯竭",
}

REGIME_BUCKET_LABELS: dict[str, str] = {
    "trend_up": "趋势上涨",
    "high_vol": "高波动",
    "oscillation": "震荡",
    "trend_down": "趋势下跌",
}

# 四格 bucket 顺序（L2 矩阵 / 策略推荐主维度）
REGIME_BUCKET_ORDER = ["trend_up", "high_vol", "oscillation", "trend_down"]

# 四格 → 推荐策略（L3 硬规则；须为 backtest_engine 已支持策略）
REGIME_BUCKET_STRATEGY_MAP: dict[str, str] = {
    "trend_up": "momentum",
    "high_vol": "turtle",
    "oscillation": "composite",
    "trend_down": "dividend_defensive",
}


def regime_bucket(regime: str, price_vs_ma60: float = 0.0) -> str:
    """四格简化标签（v2）：high_volatility 独立成格，不再按 MA60 方向拆分。

    七格 regime 仍用于 V5 权重 / 细粒度分析；四格用于 L2 绩效矩阵与策略推荐。
    price_vs_ma60 保留参数以兼容旧调用，不再参与映射。
    """
    _ = price_vs_ma60  # 保留签名，避免按方向拆分高波动
    if regime in ("strong_trend_up", "weak_trend_up"):
        return "trend_up"
    if regime == "high_volatility":
        return "high_vol"
    if regime in ("strong_trend_down", "weak_trend_down", "liquidity_drought"):
        return "trend_down"
    return "oscillation"


def regime_bucket_label(bucket: str) -> str:
    return REGIME_BUCKET_LABELS.get(bucket, bucket)

REGIME_DIM_LABELS: dict[str, str] = {
    "fundamental": "基本面",
    "quality": "质量",
    "industry": "行业",
    "capital": "资金",
    "valuation": "估值",
    "technical": "技术",
    "market_env": "大盘",
    "policy": "政策",
    "news": "新闻",
    "mood": "情绪",
}

REGIME_GUIDANCE: dict[str, dict[str, Any]] = {
    "strong_trend_up": {
        "max_position": 0.90,
        "stop_width_mult": 1.0,
        "note": "趋势友好，可正常仓位",
    },
    "weak_trend_up": {
        "max_position": 0.85,
        "stop_width_mult": 1.0,
        "note": "温和上涨，可维持偏高仓位",
    },
    "strong_trend_down": {
        "max_position": 0.50,
        "stop_width_mult": 1.2,
        "note": "偏空，降仓、放宽止损",
    },
    "weak_trend_down": {
        "max_position": 0.60,
        "stop_width_mult": 1.1,
        "note": "弱势，适度降仓",
    },
    "oscillation": {
        "max_position": 0.70,
        "stop_width_mult": 0.9,
        "note": "震荡，控制仓位、缩短持股",
    },
    "high_volatility": {
        "max_position": 0.40,
        "stop_width_mult": 1.5,
        "note": "高波动，轻仓、宽止损",
    },
    "liquidity_drought": {
        "max_position": 0.30,
        "stop_width_mult": 1.0,
        "note": "流动性差，少交易",
    },
}

CORR_SAMPLE_SIZE = 120
REGIME_MIN_BARS = 65


@dataclass(frozen=True)
class RegimeThresholds:
    """L1 分类阈值（可通过 config / tune 脚本覆盖）。"""

    vol_high: float = 0.17
    vol_expansion: bool = True
    avg_corr_high: float = 0.65
    adx_chop: float = 50.0
    ret20_chop_abs: float = 0.03
    liquidity_amount_low: float = 0.55
    liquidity_amount_soft: float = 0.60
    liquidity_vol_cap: float = 0.28
    trend_ret20_up: float = 0.015
    trend_ret20_down: float = -0.015
    trend_ret20_strong: float = 0.08
    trend_ret20_strong_down: float = -0.08
    trend_ret60_strong: float = 0.03
    trend_ret60_strong_down: float = -0.03
    trend_rsi_strong_up: float = 52.0
    trend_rsi_strong_down: float = 48.0
    trend_ma20_slope_min: float = 0.0
    trend_priority_over_vol: bool = True


def default_regime_thresholds() -> RegimeThresholds:
    return RegimeThresholds(
        vol_high=config.REGIME_VOL_HIGH,
        vol_expansion=config.REGIME_VOL_EXPANSION,
        avg_corr_high=config.REGIME_AVG_CORR_HIGH,
        adx_chop=config.REGIME_ADX_CHOP,
        ret20_chop_abs=config.REGIME_RET20_CHOP_ABS,
        trend_ret20_up=config.REGIME_TREND_RET20_UP,
        trend_ret20_down=config.REGIME_TREND_RET20_DOWN,
        trend_ret20_strong=config.REGIME_TREND_RET20_STRONG,
        trend_ret20_strong_down=config.REGIME_TREND_RET20_STRONG_DOWN,
        trend_ret60_strong=config.REGIME_TREND_RET60_STRONG,
        trend_ret60_strong_down=config.REGIME_TREND_RET60_STRONG_DOWN,
        trend_rsi_strong_up=config.REGIME_TREND_RSI_UP,
        trend_rsi_strong_down=config.REGIME_TREND_RSI_DOWN,
        trend_priority_over_vol=config.REGIME_TREND_PRIORITY_OVER_VOL,
    )


def regime_label(regime: str) -> str:
    return REGIME_LABELS.get(regime, regime)


def get_regime_guidance(regime: str) -> dict[str, Any]:
    """Phase C：仓位与止损建议。"""
    base = REGIME_GUIDANCE.get(regime) or REGIME_GUIDANCE["oscillation"]
    return {
        "regime": regime,
        "regime_label": regime_label(regime),
        "max_position": base["max_position"],
        "stop_width_mult": base["stop_width_mult"],
        "note": base["note"],
    }


def describe_regime_weight_deltas(regime: str) -> str:
    """Phase C：V5 动态权重调整的一句话说明。"""
    deltas = config.V5_REGIME_WEIGHT_DELTAS.get(regime, {})
    if not deltas:
        return "权重保持基线，无额外调整"
    up = sorted(
        ((REGIME_DIM_LABELS.get(k, k), v) for k, v in deltas.items() if v > 0),
        key=lambda x: -x[1],
    )
    down = sorted(
        ((REGIME_DIM_LABELS.get(k, k), v) for k, v in deltas.items() if v < 0),
        key=lambda x: x[1],
    )
    parts: list[str] = []
    if up:
        parts.append("加重" + "、".join(label for label, _ in up))
    if down:
        parts.append("减轻" + "、".join(label for label, _ in down))
    return "，".join(parts)


def enrich_regime_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """为 API 响应附加 guidance 与 V5 权重说明。"""
    regime = str(payload.get("regime") or "oscillation")
    payload.setdefault("regime_label", regime_label(regime))
    payload["guidance"] = get_regime_guidance(regime)
    payload["weight_note"] = describe_regime_weight_deltas(regime)
    return payload


def _index_snapshot(result: dict[str, Any], index_code: str) -> dict[str, Any]:
    """单指数 regime 结果 → API 子结构。"""
    regime = str(result.get("regime") or "oscillation")
    pv60 = float(result.get("price_vs_ma60") or 0.0)
    bucket = regime_bucket(regime, pv60)
    from services.market_index import resolve_index_code

    resolved = resolve_index_code(index_code)
    name = resolved[1] if resolved else index_code
    return {
        "index_code": index_code,
        "index_name": name,
        "regime": regime,
        "regime_label": result.get("regime_label") or regime_label(regime),
        "regime_bucket": bucket,
        "regime_bucket_label": regime_bucket_label(bucket),
        "rsi_14": result.get("rsi_14"),
        "volatility_20": result.get("volatility_20"),
        "adx": result.get("adx"),
        "return_20d": result.get("return_20d"),
        "return_60d": result.get("return_60d"),
        "price_vs_ma20": result.get("price_vs_ma20"),
        "price_vs_ma60": result.get("price_vs_ma60"),
        "ma20_slope": result.get("ma20_slope"),
    }


def enrich_dual_regime_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """双轨 API：CSI300 + CSI800 并列展示，主基准由 config.REGIME_PRIMARY_INDEX 决定。"""
    csi300 = _index_snapshot(payload, config.REGIME_INDEX_CSI300)
    csi800_raw = {
        "regime": payload.get("regime_csi800") or payload.get("regime"),
        "regime_label": payload.get("regime_csi800_label"),
        "rsi_14": payload.get("rsi_14_csi800"),
        "volatility_20": payload.get("volatility_20_csi800"),
        "adx": payload.get("adx_csi800"),
        "return_20d": payload.get("return_20d_csi800"),
        "return_60d": payload.get("return_60d_csi800"),
        "price_vs_ma20": payload.get("price_vs_ma20_csi800"),
        "price_vs_ma60": payload.get("price_vs_ma60_csi800"),
        "ma20_slope": payload.get("ma20_slope_csi800"),
    }
    csi800 = _index_snapshot(csi800_raw, config.REGIME_INDEX_CSI800)

    label_agree = csi300["regime_label"] == csi800["regime_label"]
    bucket_agree = csi300["regime_bucket"] == csi800["regime_bucket"]
    primary_code = config.REGIME_PRIMARY_INDEX
    primary = csi800 if primary_code == config.REGIME_INDEX_CSI800 else csi300

    payload["regime_csi300"] = csi300["regime"]
    payload["regime_csi300_label"] = csi300["regime_label"]
    payload["regime_csi800"] = csi800["regime"]
    payload["regime_csi800_label"] = csi800["regime_label"]
    payload["regime_bucket_csi300"] = csi300["regime_bucket"]
    payload["regime_bucket_csi800"] = csi800["regime_bucket"]
    payload["regime_label_agreement"] = label_agree
    payload["regime_bucket_agreement"] = bucket_agree
    payload["indices"] = [csi300, csi800]
    payload["primary_index"] = primary_code
    payload["primary_regime"] = primary["regime"]
    payload["primary_regime_label"] = primary["regime_label"]
    payload["primary_regime_bucket"] = primary["regime_bucket"]
    payload["primary_regime_bucket_label"] = primary["regime_bucket_label"]

    # 顶层 regime 仍指向 CSI300，兼容 V5 / 动量门控等现有逻辑
    payload.setdefault("regime", csi300["regime"])
    payload.setdefault("regime_label", csi300["regime_label"])
    payload["guidance"] = get_regime_guidance(str(payload.get("regime") or "oscillation"))
    payload["weight_note"] = describe_regime_weight_deltas(str(payload.get("regime") or "oscillation"))
    return payload


def _is_valid(v: Any) -> bool:
    if v is None:
        return False
    try:
        f = float(v)
        return math.isfinite(f)
    except (TypeError, ValueError):
        return False


def _rsi(closes: list[float], window: int = 14) -> float:
    if len(closes) < window + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(1, window + 1):
        diff = closes[-window - 1 + i] - closes[-window - 2 + i]
        if diff > 0:
            gains.append(diff)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(-diff)
    avg_gain = sum(gains) / window
    avg_loss = sum(losses) / window
    if avg_loss < 1e-12:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _volatility(closes: list[float], window: int = 20) -> float:
    if len(closes) < window + 1:
        return 0.0
    rets = [
        closes[i] / closes[i - 1] - 1
        for i in range(-window, 0)
        if closes[i - 1] > 0
    ]
    if len(rets) < window:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return math.sqrt(var) * math.sqrt(252)


def _ma(closes: list[float], window: int) -> float:
    if len(closes) < window:
        return sum(closes) / len(closes) if closes else 0.0
    return sum(closes[-window:]) / window


def _pct_ret(closes: list[float], lag: int) -> float:
    if len(closes) <= lag or closes[-lag - 1] <= 0:
        return 0.0
    return closes[-1] / closes[-lag - 1] - 1


def _adx(highs: list[float], lows: list[float], closes: list[float], window: int = 14) -> float:
    if len(closes) < window * 2 + 1:
        return 25.0
    trs = []
    plus_dms = []
    minus_dms = []
    for i in range(-window, 0):
        h, l = highs[i], lows[i]
        prev_c = closes[i - 1]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        plus_dm = max(h - highs[i - 1], 0)
        minus_dm = max(lows[i - 1] - l, 0)
        trs.append(tr)
        plus_dms.append(plus_dm)
        minus_dms.append(minus_dm)
    atr = sum(trs) / len(trs) if trs else 1e-9
    plus_di = 100 * sum(plus_dms) / (atr * window) if atr > 0 else 0
    minus_di = 100 * sum(minus_dms) / (atr * window) if atr > 0 else 0
    return abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9) * 100


def _pearson(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n < 3 or len(y) != n:
        return 0.0
    mx = sum(x) / n
    my = sum(y) / n
    num = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    den = math.sqrt(sum((x[i] - mx) ** 2 for i in range(n)) * sum((y[i] - my) ** 2 for i in range(n)))
    return num / den if den > 1e-12 else 0.0


def _spearman(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n < 3 or len(y) != n:
        return 0.0

    def _rank(values: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: values[i])
        ranks = [0.0] * n
        for ri, idx in enumerate(order):
            ranks[idx] = ri + 1.0
        return ranks

    return _pearson(_rank(x), _rank(y))


def _ma20_slope(closes: list[float]) -> float:
    if len(closes) < 25:
        return 0.0
    ma_now = _ma(closes, 20)
    ma_prev = sum(closes[-25:-5]) / 20 if len(closes) >= 25 else ma_now
    if abs(ma_prev) < 1e-12:
        return 0.0
    return ma_now / ma_prev - 1


def _trade_dates(conn: sqlite3.Connection, trade_date: str, limit: int) -> list[str]:
    rows = conn.execute(
        """SELECT DISTINCT trade_date FROM stock_daily_quotes
           WHERE trade_date <= ? AND close IS NOT NULL
           ORDER BY trade_date DESC LIMIT ?""",
        (trade_date, limit),
    ).fetchall()
    return [r[0] for r in rows]


def _compute_ad_ratio(conn: sqlite3.Connection, trade_date: str) -> Optional[float]:
    prev = conn.execute(
        "SELECT MAX(trade_date) FROM stock_daily_quotes WHERE trade_date < ? AND close IS NOT NULL",
        (trade_date,),
    ).fetchone()
    if not prev or not prev[0]:
        return None
    row = conn.execute(
        """SELECT
             SUM(CASE WHEN q0.close > q1.close THEN 1 ELSE 0 END),
             SUM(CASE WHEN q0.close < q1.close THEN 1 ELSE 0 END)
           FROM stock_daily_quotes q0
           JOIN stock_daily_quotes q1 ON q0.stock_id = q1.stock_id AND q1.trade_date = ?
           WHERE q0.trade_date = ? AND q0.close IS NOT NULL AND q1.close IS NOT NULL AND q1.close > 0""",
        (prev[0], trade_date),
    ).fetchone()
    if not row or row[0] is None:
        return None
    adv, dec = int(row[0] or 0), int(row[1] or 0)
    total = adv + dec
    if total <= 0:
        return None
    return adv / total


def _compute_amount_metrics(conn: sqlite3.Connection, trade_date: str) -> tuple[Optional[float], Optional[float]]:
    rows = conn.execute(
        """SELECT trade_date, SUM(COALESCE(amount, 0)) AS total
           FROM stock_daily_quotes
           WHERE trade_date <= ? AND close IS NOT NULL
           GROUP BY trade_date
           ORDER BY trade_date DESC LIMIT 21""",
        (trade_date,),
    ).fetchall()
    if len(rows) < 6:
        return None, None
    totals = [float(r[1] or 0) for r in rows if float(r[1] or 0) > 0]
    if len(totals) < 6:
        return None, None
    today = totals[0]
    hist = totals[1:21]
    if not hist:
        return None, None
    ma20 = sum(hist) / len(hist)
    amount_ratio_20 = today / ma20 if ma20 > 0 else None
    recent5 = totals[:5][::-1]
    if len(recent5) >= 2:
        xs = list(range(len(recent5)))
        mx = sum(xs) / len(xs)
        my = sum(recent5) / len(recent5)
        num = sum((xs[i] - mx) * (recent5[i] - my) for i in range(len(recent5)))
        den = sum((xs[i] - mx) ** 2 for i in range(len(recent5)))
        amount_slope_5 = num / den if den > 1e-12 else 0.0
    else:
        amount_slope_5 = 0.0
    return amount_ratio_20, amount_slope_5


def _industry_returns(
    conn: sqlite3.Connection,
    end_date: str,
    start_date: str,
) -> dict[str, float]:
    rows = conn.execute(
        """SELECT s.industry_sw, q0.close AS c0, q1.close AS c1
           FROM stocks s
           JOIN stock_daily_quotes q0 ON q0.stock_id = s.id AND q0.trade_date = ?
           JOIN stock_daily_quotes q1 ON q1.stock_id = s.id AND q1.trade_date = ?
           WHERE s.is_active = 1 AND s.industry_sw IS NOT NULL AND s.industry_sw != ''
             AND q0.close IS NOT NULL AND q1.close IS NOT NULL AND q1.close > 0""",
        (end_date, start_date),
    ).fetchall()
    by_ind: dict[str, list[float]] = {}
    for ind, c0, c1 in rows:
        if not ind:
            continue
        by_ind.setdefault(str(ind), []).append(float(c0) / float(c1) - 1)
    return {ind: sum(v) / len(v) for ind, v in by_ind.items() if v}


def _compute_rotation_speed(conn: sqlite3.Connection, trade_date: str) -> Optional[float]:
    dates = _trade_dates(conn, trade_date, 11)
    if len(dates) < 6:
        return None
    recent = _industry_returns(conn, dates[0], dates[5])
    prior = _industry_returns(conn, dates[5], dates[min(10, len(dates) - 1)])
    common = sorted(set(recent.keys()) & set(prior.keys()))
    if len(common) < 5:
        return None
    x = [recent[i] for i in common]
    y = [prior[i] for i in common]
    corr = _spearman(x, y)
    return max(0.0, min(1.0, 1.0 - corr))


def _compute_avg_corr(conn: sqlite3.Connection, trade_date: str) -> Optional[float]:
    dates = _trade_dates(conn, trade_date, 21)
    if len(dates) < 21:
        return None
    date_set = set(dates)
    rows = conn.execute(
        """SELECT stock_id, trade_date, close
           FROM stock_daily_quotes
           WHERE trade_date IN ({}) AND close IS NOT NULL AND close > 0
           ORDER BY stock_id, trade_date""".format(",".join("?" * len(dates))),
        tuple(dates),
    ).fetchall()
    by_sid: dict[int, dict[str, float]] = {}
    for sid, td, close in rows:
        by_sid.setdefault(int(sid), {})[td] = float(close)

    candidates = [
        sid for sid, px in by_sid.items()
        if len(px) >= 21 and dates[0] in px and dates[-1] in px
    ]
    if len(candidates) < 20:
        return None
    # 按最近一日成交额抽样，降低计算量
    amt_rows = conn.execute(
        """SELECT stock_id, amount FROM stock_daily_quotes
           WHERE trade_date = ? AND amount IS NOT NULL
           ORDER BY amount DESC LIMIT ?""",
        (dates[0], CORR_SAMPLE_SIZE * 2),
    ).fetchall()
    sample_ids = [int(r[0]) for r in amt_rows if int(r[0]) in by_sid][:CORR_SAMPLE_SIZE]
    if len(sample_ids) < 20:
        sample_ids = candidates[:CORR_SAMPLE_SIZE]

    ordered_dates = sorted(date_set)
    series: list[list[float]] = []
    for sid in sample_ids:
        px = by_sid[sid]
        closes = [px[d] for d in ordered_dates if d in px]
        if len(closes) < 21:
            continue
        rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1] > 0]
        if len(rets) >= 20:
            series.append(rets[-20:])
    if len(series) < 10:
        return None

    corrs: list[float] = []
    n = len(series)
    for i in range(n):
        for j in range(i + 1, n):
            c = _pearson(series[i], series[j])
            if math.isfinite(c):
                corrs.append(c)
    return sum(corrs) / len(corrs) if corrs else None


def compute_market_features(conn: sqlite3.Connection, trade_date: str) -> dict[str, Any]:
    """Phase A：全市场广度特征（仅使用 trade_date 及之前数据）。"""
    ad_ratio = _compute_ad_ratio(conn, trade_date)
    amount_ratio_20, amount_slope_5 = _compute_amount_metrics(conn, trade_date)
    rotation_speed = _compute_rotation_speed(conn, trade_date)
    avg_corr_20 = _compute_avg_corr(conn, trade_date)

    liquidity_score = None
    if amount_ratio_20 is not None:
        liquidity_score = max(0.0, min(1.0, amount_ratio_20))

    return {
        "ad_ratio": ad_ratio,
        "amount_ratio_20": amount_ratio_20,
        "amount_slope_5": amount_slope_5,
        "rotation_speed": rotation_speed,
        "avg_corr_20": avg_corr_20,
        "liquidity_score": liquidity_score,
    }


def _classify_index_only(
    rsi: float,
    vol: float,
    adx: float,
    ret20: float,
    ret60: float,
    price_vs_ma20: float,
    price_vs_ma60: float,
    ma20_slope: float,
    *,
    vol60: Optional[float] = None,
    thresholds: Optional[RegimeThresholds] = None,
) -> str:
    """无全市场特征时的指数规则（兼容旧逻辑）。"""
    th = thresholds or default_regime_thresholds()
    v60 = vol60 if vol60 is not None else vol
    if th.trend_priority_over_vol:
        trend = _classify_trend_layer(
            rsi, ret20, ret60, price_vs_ma20, price_vs_ma60, ma20_slope, None, th,
            strong_only=True,
        )
        if trend:
            return trend
    if _is_high_volatility(vol, v60, adx, ret20, th, avg_corr_20=None):
        return "high_volatility"
    trend = _classify_trend_layer(
        rsi, ret20, ret60, price_vs_ma20, price_vs_ma60, ma20_slope, None, th,
    )
    if trend:
        return trend
    if price_vs_ma60 < config.REGIME_TREND_DOWN_PVM60 and ret20 <= th.trend_ret20_down:
        return "weak_trend_down"
    return "oscillation"


def _is_high_volatility(
    vol: float,
    vol60: float,
    adx: float,
    ret20: float,
    thresholds: RegimeThresholds,
    *,
    avg_corr_20: Optional[float],
) -> bool:
    if vol > thresholds.vol_high:
        if not thresholds.vol_expansion or vol > vol60:
            return True
    if avg_corr_20 is not None and avg_corr_20 > thresholds.avg_corr_high:
        return True
    if adx > thresholds.adx_chop and abs(ret20) < thresholds.ret20_chop_abs:
        return True
    return False


def _classify_trend_layer(
    rsi: float,
    ret20: float,
    ret60: float,
    price_vs_ma20: float,
    price_vs_ma60: float,
    ma20_slope: float,
    features: Optional[dict[str, Any]],
    thresholds: RegimeThresholds,
    *,
    strong_only: bool = False,
) -> Optional[str]:
    """趋势层：强方向性运动优先于高波动；未命中返回 None。"""
    th = thresholds
    ad_ratio = (features or {}).get("ad_ratio")
    rotation_speed = (features or {}).get("rotation_speed")
    breadth_up = ad_ratio is None or ad_ratio > 0.52
    breadth_down = ad_ratio is None or ad_ratio < 0.48
    fast_rotation = rotation_speed is not None and rotation_speed > 0.70

    if (
        ret20 >= th.trend_ret20_strong
        and ma20_slope > th.trend_ma20_slope_min
        and price_vs_ma20 > 0
    ):
        if (
            ret60 >= th.trend_ret60_strong
            and price_vs_ma60 > 0
            and rsi >= th.trend_rsi_strong_up
        ):
            return "strong_trend_up"
        return "weak_trend_up"

    if (
        ret20 <= th.trend_ret20_strong_down
        and ma20_slope < -th.trend_ma20_slope_min
        and price_vs_ma20 < 0
    ):
        if (
            ret60 <= th.trend_ret60_strong_down
            and price_vs_ma60 < 0
            and rsi <= th.trend_rsi_strong_down
        ):
            return "strong_trend_down"
        return "weak_trend_down"

    if strong_only:
        return None

    if (
        ret20 >= th.trend_ret20_up
        and ma20_slope > 0
        and price_vs_ma20 > 0
        and breadth_up
        and not fast_rotation
    ):
        if (
            ret60 >= th.trend_ret60_strong
            and price_vs_ma60 > 0
            and rsi >= th.trend_rsi_strong_up
        ):
            return "strong_trend_up"
        return "weak_trend_up"

    if (
        ret20 <= th.trend_ret20_down
        and ma20_slope < 0
        and price_vs_ma20 < 0
        and breadth_down
        and not fast_rotation
    ):
        if (
            ret60 <= th.trend_ret60_strong_down
            and price_vs_ma60 < 0
            and rsi <= th.trend_rsi_strong_down
        ):
            return "strong_trend_down"
        return "weak_trend_down"

    return None


def _classify_with_features(
    rsi: float,
    vol: float,
    adx: float,
    ret20: float,
    ret60: float,
    price_vs_ma20: float,
    price_vs_ma60: float,
    ma20_slope: float,
    features: dict[str, Any],
    *,
    vol60: Optional[float] = None,
    thresholds: Optional[RegimeThresholds] = None,
) -> str:
    """Phase B：流动性 → 趋势（优先）→ 高波动 → 震荡。"""
    th = thresholds or default_regime_thresholds()
    v60 = vol60 if vol60 is not None else vol
    ad_ratio = features.get("ad_ratio")
    amount_ratio_20 = features.get("amount_ratio_20")
    avg_corr_20 = features.get("avg_corr_20")

    if amount_ratio_20 is not None and amount_ratio_20 < th.liquidity_amount_soft and vol < th.liquidity_vol_cap:
        return "liquidity_drought"
    if amount_ratio_20 is not None and amount_ratio_20 < th.liquidity_amount_low:
        return "liquidity_drought"

    if th.trend_priority_over_vol:
        trend = _classify_trend_layer(
            rsi, ret20, ret60, price_vs_ma20, price_vs_ma60, ma20_slope, features, th,
            strong_only=True,
        )
        if trend:
            return trend

    if _is_high_volatility(vol, v60, adx, ret20, th, avg_corr_20=avg_corr_20):
        return "high_volatility"

    trend = _classify_trend_layer(
        rsi, ret20, ret60, price_vs_ma20, price_vs_ma60, ma20_slope, features, th,
    )
    if trend:
        return trend

    if ret20 > 0.02 and ad_ratio is not None and ad_ratio < 0.50:
        return "oscillation"
    if ret20 < -0.02 and ad_ratio is not None and ad_ratio > 0.50:
        return "oscillation"

    if price_vs_ma60 < config.REGIME_TREND_DOWN_PVM60 and ret20 <= th.trend_ret20_down:
        return "weak_trend_down"

    return "oscillation"


def classify_regime(
    kline: list[dict[str, Any]],
    features: Optional[dict[str, Any]] = None,
    *,
    thresholds: Optional[RegimeThresholds] = None,
) -> dict[str, Any]:
    """基于指数 K 线 + 可选全市场特征分类市场状态。"""
    empty = {
        "regime": "oscillation",
        "regime_label": regime_label("oscillation"),
        "rsi_14": 50.0,
        "volatility_20": 0.0,
        "volatility_60": 0.0,
        "adx": 25.0,
        "return_20d": 0.0,
        "return_60d": 0.0,
        "price_vs_ma20": 0.0,
        "price_vs_ma60": 0.0,
        "ma20_slope": 0.0,
    }
    if not kline or len(kline) < REGIME_MIN_BARS:
        return {**empty, **(features or {})}

    closes = [float(b["close"]) for b in kline if _is_valid(b.get("close"))]
    highs = [float(b["high"]) for b in kline if _is_valid(b.get("high"))]
    lows = [float(b["low"]) for b in kline if _is_valid(b.get("low"))]
    if len(closes) < REGIME_MIN_BARS:
        return {**empty, **(features or {})}

    rsi = _rsi(closes, 14)
    vol = _volatility(closes, 20)
    vol60 = _volatility(closes, 60) if len(closes) >= 61 else vol
    adx = _adx(highs, lows, closes, 14)
    ret20 = _pct_ret(closes, 20)
    ret60 = _pct_ret(closes, 60)
    ma20 = _ma(closes, 20)
    ma60 = _ma(closes, 60)
    price = closes[-1]
    price_vs_ma20 = (price / ma20 - 1) if ma20 > 0 else 0.0
    price_vs_ma60 = (price / ma60 - 1) if ma60 > 0 else 0.0
    ma20_slope = _ma20_slope(closes)

    th = thresholds or default_regime_thresholds()
    use_features = features is not None and features.get("ad_ratio") is not None
    if use_features:
        regime = _classify_with_features(
            rsi, vol, adx, ret20, ret60, price_vs_ma20, price_vs_ma60, ma20_slope, features,
            vol60=vol60, thresholds=th,
        )
    else:
        regime = _classify_index_only(
            rsi, vol, adx, ret20, ret60, price_vs_ma20, price_vs_ma60, ma20_slope,
            vol60=vol60, thresholds=th,
        )

    out = {
        "regime": regime,
        "regime_label": regime_label(regime),
        "rsi_14": rsi,
        "volatility_20": vol,
        "volatility_60": vol60,
        "adx": adx,
        "return_20d": ret20,
        "return_60d": ret60,
        "price_vs_ma20": price_vs_ma20,
        "price_vs_ma60": price_vs_ma60,
        "ma20_slope": ma20_slope,
    }
    if features:
        out.update({k: v for k, v in features.items() if k not in out})
    return out


def classify_regime_state(
    *,
    rsi: float,
    vol: float,
    vol60: float,
    adx: float,
    ret20: float,
    ret60: float,
    price_vs_ma20: float,
    price_vs_ma60: float,
    ma20_slope: float,
    features: Optional[dict[str, Any]] = None,
    thresholds: Optional[RegimeThresholds] = None,
) -> str:
    """用已算好的指标 + 可选广度特征分类（供阈值调参脚本复用）。"""
    th = thresholds or default_regime_thresholds()
    use_features = features is not None and features.get("ad_ratio") is not None
    if use_features:
        return _classify_with_features(
            rsi, vol, adx, ret20, ret60, price_vs_ma20, price_vs_ma60, ma20_slope, features,
            vol60=vol60, thresholds=th,
        )
    return _classify_index_only(
        rsi, vol, adx, ret20, ret60, price_vs_ma20, price_vs_ma60, ma20_slope,
        vol60=vol60, thresholds=th,
    )


def _prepare_kline(
    index_code: str,
    trade_date: Optional[str] = None,
    *,
    days: Optional[int] = None,
) -> tuple[list[dict[str, Any]], Optional[str], Optional[str]]:
    """拉取并裁剪 K 线，返回 (kline, last_date, error)。

    历史 trade_date 必须能截到 >= REGIME_MIN_BARS 根 K 线；否则返回 error，
    避免静默使用「最新 K 线」污染历史标签（曾导致 80% 伪高波动）。
    """
    fetch_days = days or config.REGIME_KLINE_DAYS
    if trade_date:
        fetch_days = max(fetch_days, config.REGIME_KLINE_DAYS, 400)

    data = fetch_index_kline(
        index_code,
        period="daily",
        days=fetch_days,
        with_technical=False,
        end_date=trade_date,
        force=bool(trade_date),
    )
    kline = data.get("kline") or []
    if not kline:
        return [], None, data.get("error", "无指数数据")

    last_date = kline[-1].get("date") if kline else None
    if trade_date and last_date and last_date != trade_date:
        truncated = [b for b in kline if b.get("date") <= trade_date]
        if len(truncated) >= REGIME_MIN_BARS:
            kline = truncated
            last_date = truncated[-1].get("date")
        elif fetch_days < 1200:
            return _prepare_kline(index_code, trade_date, days=min(fetch_days * 2, 1200))
        else:
            return (
                [],
                None,
                f"trade_date={trade_date} 可用 K 线不足 ({len(truncated)}<{REGIME_MIN_BARS})",
            )
    elif trade_date and last_date and last_date == trade_date and len(kline) < REGIME_MIN_BARS:
        if fetch_days < 1200:
            return _prepare_kline(index_code, trade_date, days=min(fetch_days * 2, 1200))
        return [], None, f"trade_date={trade_date} K 线不足 ({len(kline)}<{REGIME_MIN_BARS})"

    return kline, last_date, None


def detect_regime(
    trade_date: Optional[str] = None,
    index_code: str = "sh000300",
    conn: Optional[sqlite3.Connection] = None,
    *,
    kline: Optional[list[dict[str, Any]]] = None,
    features: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """拉取指数 K 线并分类。trade_date 仅用于校验最后一条日期。"""
    own_conn = None
    if kline is None:
        kline, last_date, err = _prepare_kline(index_code, trade_date)
        if err:
            return {
                "regime": "oscillation",
                "regime_label": regime_label("oscillation"),
                "index_code": index_code,
                "error": err,
            }
    else:
        last_date = kline[-1].get("date") if kline else None

    if features is None and last_date:
        try:
            if conn is None:
                own_conn = sqlite3.connect(config.DB_PATH, timeout=30)
                conn = own_conn
            features = compute_market_features(conn, last_date)
        except Exception:
            features = None
        finally:
            if own_conn is not None:
                own_conn.close()

    result = classify_regime(kline or [], features=features)
    result["trade_date"] = last_date
    result["index_code"] = index_code
    return enrich_regime_payload(result)


def detect_regime_dual(
    trade_date: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> dict[str, Any]:
    """双轨：CSI300 + CSI800 共用全市场广度特征，分别按各自 K 线分类。"""
    own_conn = None
    if conn is None:
        own_conn = sqlite3.connect(config.DB_PATH, timeout=30)
        conn = own_conn

    try:
        k300, dt300, err300 = _prepare_kline(config.REGIME_INDEX_CSI300, trade_date)
        k800, dt800, err800 = _prepare_kline(config.REGIME_INDEX_CSI800, trade_date)
        trade_dt = trade_date or dt300 or dt800
        if not trade_dt:
            return {"error": err300 or err800 or "无指数数据", "regime": "oscillation"}

        features = None
        try:
            features = compute_market_features(conn, trade_dt)
        except Exception:
            features = None

        r300 = classify_regime(k300, features=features) if len(k300) >= 65 else {
            "regime": "oscillation",
            "regime_label": regime_label("oscillation"),
            "error": err300,
        }
        r800 = classify_regime(k800, features=features) if len(k800) >= 65 else {
            "regime": "oscillation",
            "regime_label": regime_label("oscillation"),
            "error": err800,
        }

        bucket300 = regime_bucket(r300["regime"], float(r300.get("price_vs_ma60") or 0))
        bucket800 = regime_bucket(r800["regime"], float(r800.get("price_vs_ma60") or 0))
        label300 = r300.get("regime_label") or regime_label(r300["regime"])
        label800 = r800.get("regime_label") or regime_label(r800["regime"])

        out: dict[str, Any] = {
            "trade_date": trade_dt,
            "index_code": config.REGIME_INDEX_CSI300,
            "regime": r300["regime"],
            "regime_label": label300,
            "rsi_14": r300.get("rsi_14"),
            "volatility_20": r300.get("volatility_20"),
            "adx": r300.get("adx"),
            "return_20d": r300.get("return_20d"),
            "return_60d": r300.get("return_60d"),
            "price_vs_ma20": r300.get("price_vs_ma20"),
            "price_vs_ma60": r300.get("price_vs_ma60"),
            "ma20_slope": r300.get("ma20_slope"),
            "regime_csi800": r800["regime"],
            "regime_csi800_label": label800,
            "regime_bucket_csi300": bucket300,
            "regime_bucket_csi800": bucket800,
            "regime_label_agreement": 1 if label300 == label800 else 0,
            "regime_bucket_agreement": 1 if bucket300 == bucket800 else 0,
            "rsi_14_csi800": r800.get("rsi_14"),
            "volatility_20_csi800": r800.get("volatility_20"),
            "adx_csi800": r800.get("adx"),
            "return_20d_csi800": r800.get("return_20d"),
            "return_60d_csi800": r800.get("return_60d"),
            "price_vs_ma20_csi800": r800.get("price_vs_ma20"),
            "price_vs_ma60_csi800": r800.get("price_vs_ma60"),
            "ma20_slope_csi800": r800.get("ma20_slope"),
        }
        if features:
            out.update(features)
        if err300:
            out["error_csi300"] = err300
        if err800:
            out["error_csi800"] = err800
        return enrich_dual_regime_payload(out)
    finally:
        if own_conn is not None:
            own_conn.close()


def get_regime_for_date(
    conn: sqlite3.Connection,
    trade_date: Optional[str] = None,
) -> dict[str, Any]:
    """读取某日期市场状态；不传日期取最新。"""
    if trade_date is None:
        row = conn.execute(
            "SELECT * FROM market_regime_daily ORDER BY trade_date DESC LIMIT 1"
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM market_regime_daily WHERE trade_date=? LIMIT 1", (trade_date,)
        ).fetchone()
    if not row:
        return enrich_regime_payload({"regime": "oscillation", "regime_label": regime_label("oscillation")})
    if hasattr(row, "keys"):
        out = dict(row)
    else:
        cols = [d[1] for d in conn.execute("PRAGMA table_info(market_regime_daily)")]
        out = dict(zip(cols, row))
    if not out.get("regime_label"):
        out["regime_label"] = regime_label(str(out.get("regime", "oscillation")))
    if out.get("regime_csi800"):
        return enrich_dual_regime_payload(out)
    return enrich_regime_payload(out)


def get_regime_layers_for_date(
    conn: sqlite3.Connection,
    trade_date: Optional[str] = None,
) -> dict[str, Any]:
    """并列读取规则 / Jump / HMM 四格标签（Dashboard 对照条）。"""
    regime = get_regime_for_date(conn, trade_date)
    td = regime.get("trade_date") or trade_date
    rule_bucket = regime.get("regime_bucket_csi800") or regime.get("primary_regime_bucket")

    layers: dict[str, dict[str, Any]] = {
        "rules": {
            "bucket": rule_bucket,
            "bucket_label": regime_bucket_label(str(rule_bucket or "")),
            "regime_label": regime.get("regime_csi800_label") or regime.get("regime_label"),
            "role": "production",
        },
    }

    if td and conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='market_regime_jump_daily'",
    ).fetchone():
        jump_row = conn.execute(
            """SELECT regime_bucket, jump_penalty, model_version, backend
               FROM market_regime_jump_daily WHERE trade_date=? LIMIT 1""",
            (td,),
        ).fetchone()
        if jump_row and jump_row[0]:
            layers["jump"] = {
                "bucket": jump_row[0],
                "bucket_label": regime_bucket_label(jump_row[0]),
                "jump_penalty": jump_row[1],
                "model_version": jump_row[2],
                "backend": jump_row[3],
                "role": "research",
            }

    if td and conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='market_regime_hmm_daily'",
    ).fetchone():
        hmm_row = conn.execute(
            """SELECT regime_bucket, hmm_state FROM market_regime_hmm_daily
               WHERE trade_date=? LIMIT 1""",
            (td,),
        ).fetchone()
        if hmm_row and hmm_row[0]:
            layers["hmm"] = {
                "bucket": hmm_row[0],
                "bucket_label": regime_bucket_label(hmm_row[0]),
                "hmm_state": hmm_row[1],
                "role": "research",
            }

    buckets = [layers[k]["bucket"] for k in layers if layers[k].get("bucket")]
    unique_buckets = set(buckets)
    diverged: list[str] = []
    if rule_bucket:
        for name in ("jump", "hmm"):
            layer = layers.get(name)
            if layer and layer.get("bucket") and layer["bucket"] != rule_bucket:
                diverged.append(name)

    return {
        "trade_date": td,
        "layers": layers,
        "all_aligned": len(unique_buckets) <= 1 and len(buckets) >= 2,
        "diverged_layers": diverged,
        "layer_count": len(layers),
    }


def sync_regime(
    conn: sqlite3.Connection,
    trade_date: Optional[str] = None,
    index_code: Optional[str] = None,
    *,
    dual: bool = True,
    apply_persistence: bool = True,
) -> dict[str, Any]:
    """计算并写入 market_regime_daily。默认双轨（CSI300+CSI800）。"""
    if index_code and not dual:
        result = detect_regime(trade_date=trade_date, index_code=index_code, conn=conn)
        if "error" in result and not result.get("regime"):
            return result
        dt = result.get("trade_date")
        if not dt:
            return result
        conn.execute(
            """INSERT OR REPLACE INTO market_regime_daily
               (trade_date, index_code, regime, regime_label, rsi_14, volatility_20, adx,
                return_20d, return_60d, price_vs_ma20, price_vs_ma60, ma20_slope,
                ad_ratio, amount_ratio_20, amount_slope_5, rotation_speed, avg_corr_20,
                liquidity_score, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (
                dt,
                index_code,
                result["regime"],
                result.get("regime_label") or regime_label(result["regime"]),
                result["rsi_14"],
                result["volatility_20"],
                result["adx"],
                result["return_20d"],
                result["return_60d"],
                result["price_vs_ma20"],
                result["price_vs_ma60"],
                result.get("ma20_slope"),
                result.get("ad_ratio"),
                result.get("amount_ratio_20"),
                result.get("amount_slope_5"),
                result.get("rotation_speed"),
                result.get("avg_corr_20"),
                result.get("liquidity_score"),
            ),
        )
        conn.commit()
        return enrich_regime_payload(result)

    result = detect_regime_dual(trade_date=trade_date, conn=conn)
    if result.get("error") and not result.get("trade_date"):
        return result

    dt = result.get("trade_date")
    if not dt:
        return result

    conn.execute(
        """INSERT OR REPLACE INTO market_regime_daily
           (trade_date, index_code, regime, regime_label, rsi_14, volatility_20, adx,
            return_20d, return_60d, price_vs_ma20, price_vs_ma60, ma20_slope,
            ad_ratio, amount_ratio_20, amount_slope_5, rotation_speed, avg_corr_20,
            liquidity_score,
            regime_csi800, regime_csi800_label,
            regime_bucket_csi300, regime_bucket_csi800,
            regime_label_agreement, regime_bucket_agreement,
            rsi_14_csi800, volatility_20_csi800, adx_csi800,
            return_20d_csi800, return_60d_csi800,
            price_vs_ma20_csi800, price_vs_ma60_csi800, ma20_slope_csi800,
            regime_raw, regime_csi800_raw,
            regime_bucket_csi300_raw, regime_bucket_csi800_raw,
            updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                   ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
        (
            dt,
            config.REGIME_INDEX_CSI300,
            result["regime"],
            result.get("regime_label") or regime_label(result["regime"]),
            result.get("rsi_14"),
            result.get("volatility_20"),
            result.get("adx"),
            result.get("return_20d"),
            result.get("return_60d"),
            result.get("price_vs_ma20"),
            result.get("price_vs_ma60"),
            result.get("ma20_slope"),
            result.get("ad_ratio"),
            result.get("amount_ratio_20"),
            result.get("amount_slope_5"),
            result.get("rotation_speed"),
            result.get("avg_corr_20"),
            result.get("liquidity_score"),
            result.get("regime_csi800"),
            result.get("regime_csi800_label"),
            result.get("regime_bucket_csi300"),
            result.get("regime_bucket_csi800"),
            result.get("regime_label_agreement"),
            result.get("regime_bucket_agreement"),
            result.get("rsi_14_csi800"),
            result.get("volatility_20_csi800"),
            result.get("adx_csi800"),
            result.get("return_20d_csi800"),
            result.get("return_60d_csi800"),
            result.get("price_vs_ma20_csi800"),
            result.get("price_vs_ma60_csi800"),
            result.get("ma20_slope_csi800"),
            result["regime"],
            result.get("regime_csi800"),
            result.get("regime_bucket_csi300"),
            result.get("regime_bucket_csi800"),
        ),
    )
    conn.commit()

    if apply_persistence and config.REGIME_PERSISTENCE_DAYS > 1:
        recompute_regime_persistence(conn, days=max(60, config.REGIME_PERSISTENCE_DAYS * 15))

    jump_meta: dict[str, Any] | None = None
    if dt:
        try:
            from services.regime_jump import sync_jump_regime_daily

            jump_meta = sync_jump_regime_daily(conn, dt)
            if jump_meta and jump_meta.get("error"):
                logger.warning(
                    "Jump Model 预测跳过 trade_date=%s: %s (λ=%s)",
                    dt,
                    jump_meta.get("error"),
                    jump_meta.get("jump_penalty"),
                )
        except Exception as exc:
            logger.warning("Jump Model 预测失败 trade_date=%s: %s", dt, exc)

    row = get_regime_for_date(conn, dt)
    out = enrich_dual_regime_payload(row) if row.get("regime_csi800") else enrich_regime_payload(row)
    if jump_meta and not jump_meta.get("error"):
        out["jump_regime"] = jump_meta
    return out


def apply_regime_persistence(
    raw_states: list[str],
    *,
    min_days: int = 5,
    min_days_for: Optional[Any] = None,
    default: str = "oscillation",
) -> list[str]:
    """连续 N 天相同 raw 状态才确认；N 可全局或按状态不对称；短 run 继承前一已确认状态。"""
    if min_days_for is None and min_days <= 1:
        return list(raw_states)
    n = len(raw_states)
    if n == 0:
        return []

    def _required(state: str) -> int:
        if min_days_for is not None:
            return max(1, int(min_days_for(state)))
        return max(1, min_days)

    confirmed = [default] * n
    i = 0
    while i < n:
        state = raw_states[i]
        j = i + 1
        while j < n and raw_states[j] == state:
            j += 1
        need = _required(state)
        if j - i >= need:
            for k in range(i, j):
                confirmed[k] = state
        elif i > 0:
            prev = confirmed[i - 1]
            for k in range(i, j):
                confirmed[k] = prev
        i = j
    return confirmed


def persistence_min_days_for_regime(regime: str) -> int:
    """不对称确认期：trend_up 慢确认，trend_down 快确认。"""
    if not config.REGIME_ASYMMETRIC_PERSISTENCE:
        return config.REGIME_PERSISTENCE_DAYS
    bucket = regime_bucket(str(regime or "oscillation"))
    if bucket == "trend_up":
        return config.REGIME_UP_CONFIRM_DAYS
    if bucket == "trend_down":
        return config.REGIME_DOWN_CONFIRM_DAYS
    if bucket == "high_vol":
        return config.REGIME_VOL_CONFIRM_DAYS
    return config.REGIME_OSC_CONFIRM_DAYS


def recompute_regime_persistence(
    conn: sqlite3.Connection,
    *,
    days: int = 730,
    min_days: int | None = None,
    asymmetric: bool | None = None,
    min_days_for_fn: Optional[Any] = None,
) -> dict[str, Any]:
    """从 raw 列重算持续性确认后的 regime / bucket 并写回。"""
    use_asymmetric = config.REGIME_ASYMMETRIC_PERSISTENCE if asymmetric is None else asymmetric
    symmetric_days = min_days if min_days is not None else config.REGIME_PERSISTENCE_DAYS
    if not use_asymmetric and symmetric_days <= 1:
        return {"updated_rows": 0, "min_days": symmetric_days, "skipped": True}

    persist_fn = min_days_for_fn if min_days_for_fn is not None else persistence_min_days_for_regime

    cols = {r[1] for r in conn.execute("PRAGMA table_info(market_regime_daily)").fetchall()}
    if "regime_raw" not in cols:
        return {"error": "缺少 regime_raw 列，请先运行 migration v58", "updated_rows": 0}

    conn.execute(
        """UPDATE market_regime_daily SET regime_raw=regime
           WHERE regime_raw IS NULL AND regime IS NOT NULL"""
    )
    conn.execute(
        """UPDATE market_regime_daily SET regime_csi800_raw=regime_csi800
           WHERE regime_csi800_raw IS NULL AND regime_csi800 IS NOT NULL"""
    )
    conn.execute(
        """UPDATE market_regime_daily SET regime_bucket_csi300_raw=regime_bucket_csi300
           WHERE regime_bucket_csi300_raw IS NULL AND regime_bucket_csi300 IS NOT NULL"""
    )
    conn.execute(
        """UPDATE market_regime_daily SET regime_bucket_csi800_raw=regime_bucket_csi800
           WHERE regime_bucket_csi800_raw IS NULL AND regime_bucket_csi800 IS NOT NULL"""
    )

    rows = conn.execute(
        """SELECT trade_date, regime_raw, regime_csi800_raw,
                  price_vs_ma60, price_vs_ma60_csi800
           FROM market_regime_daily
           WHERE regime_csi800 IS NOT NULL OR regime IS NOT NULL
           ORDER BY trade_date DESC LIMIT ?""",
        (days,),
    ).fetchall()
    if not rows:
        return {"updated_rows": 0, "min_days": symmetric_days}

    ordered = list(reversed(rows))
    dates = [r[0] for r in ordered]
    raw300 = [str(r[1] or "oscillation") for r in ordered]
    raw800 = [str(r[2] or r[1] or "oscillation") for r in ordered]
    pv300 = [float(r[3] or 0) for r in ordered]
    pv800 = [float(r[4] if r[4] is not None else r[3] or 0) for r in ordered]

    conf300 = apply_regime_persistence(
        raw300,
        min_days=symmetric_days,
        min_days_for=persist_fn if use_asymmetric else None,
    )
    conf800 = apply_regime_persistence(
        raw800,
        min_days=symmetric_days,
        min_days_for=persist_fn if use_asymmetric else None,
    )

    before_dist: dict[str, int] = {}
    after_dist: dict[str, int] = {}
    updated = 0
    for i, td in enumerate(dates):
        b300_raw = regime_bucket(raw300[i], pv300[i])
        b800_raw = regime_bucket(raw800[i], pv800[i])
        before_dist[b800_raw] = before_dist.get(b800_raw, 0) + 1

        b300 = regime_bucket(conf300[i], pv300[i])
        b800 = regime_bucket(conf800[i], pv800[i])
        after_dist[b800] = after_dist.get(b800, 0) + 1
        label300 = regime_label(conf300[i])
        label800 = regime_label(conf800[i])
        label_agree = 1 if label300 == label800 else 0
        bucket_agree = 1 if b300 == b800 else 0

        conn.execute(
            """UPDATE market_regime_daily
               SET regime=?, regime_label=?,
                   regime_csi800=?, regime_csi800_label=?,
                   regime_bucket_csi300=?, regime_bucket_csi800=?,
                   regime_label_agreement=?, regime_bucket_agreement=?,
                   regime_raw=?, regime_csi800_raw=?,
                   regime_bucket_csi300_raw=?, regime_bucket_csi800_raw=?,
                   updated_at=datetime('now')
               WHERE trade_date=?""",
            (
                conf300[i], label300,
                conf800[i], label800,
                b300, b800,
                label_agree, bucket_agree,
                raw300[i], raw800[i],
                b300_raw, b800_raw,
                td,
            ),
        )
        updated += 1

    conn.commit()
    return {
        "updated_rows": updated,
        "asymmetric": use_asymmetric,
        "confirm_days": {
            "trend_up": config.REGIME_UP_CONFIRM_DAYS,
            "trend_down": config.REGIME_DOWN_CONFIRM_DAYS,
            "high_vol": config.REGIME_VOL_CONFIRM_DAYS,
            "oscillation": config.REGIME_OSC_CONFIRM_DAYS,
        } if use_asymmetric else None,
        "min_days": symmetric_days,
        "start_date": dates[0],
        "end_date": dates[-1],
        "bucket_distribution_raw_csi800": before_dist,
        "bucket_distribution_confirmed_csi800": after_dist,
    }


def get_regime_agreement_stats(conn: sqlite3.Connection, *, days: int = 252) -> dict[str, Any]:
    """历史双轨一致率（用于验证 CSI300 vs CSI800 差异频率）。"""
    rows = conn.execute(
        """SELECT regime_label_agreement, regime_bucket_agreement
           FROM market_regime_daily
           WHERE regime_csi800 IS NOT NULL
           ORDER BY trade_date DESC LIMIT ?""",
        (days,),
    ).fetchall()
    n = len(rows)
    if n == 0:
        return {"sample_days": 0, "label_agreement_pct": None, "bucket_agreement_pct": None}
    label_ok = sum(1 for r in rows if r[0])
    bucket_ok = sum(1 for r in rows if r[1])
    return {
        "sample_days": n,
        "label_agreement_pct": round(label_ok / n * 100, 1),
        "bucket_agreement_pct": round(bucket_ok / n * 100, 1),
        "label_disagreement_days": n - label_ok,
        "bucket_disagreement_days": n - bucket_ok,
    }


def _build_regime_segments(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """连续相同四格 bucket 合并为时间段（用于时间轴可视化）。"""
    if not rows:
        return []
    segments: list[dict[str, Any]] = []
    cur = rows[0]["bucket"]
    start = rows[0]["trade_date"]
    end = start
    days = 1
    for row in rows[1:]:
        b = row["bucket"]
        d = row["trade_date"]
        if b == cur:
            days += 1
            end = d
        else:
            segments.append({
                "bucket": cur,
                "bucket_label": regime_bucket_label(cur),
                "start_date": start,
                "end_date": end,
                "days": days,
            })
            cur = b
            start = d
            end = d
            days = 1
    segments.append({
        "bucket": cur,
        "bucket_label": regime_bucket_label(cur),
        "start_date": start,
        "end_date": end,
        "days": days,
    })
    return segments


def get_regime_history(
    conn: sqlite3.Connection,
    *,
    primary: str = "csi800",
    days: int = 730,
) -> dict[str, Any]:
    """历史四格状态序列 + 分布 + 连续段（L1 周期可视化）。"""
    from services.regime_validation import load_regime_rows

    rows = load_regime_rows(conn, primary=primary, days=max(30, min(days, 730)))
    if not rows:
        return {"error": "market_regime_daily 样本不足", "sample_days": 0}

    dist: dict[str, int] = {b: 0 for b in REGIME_BUCKET_ORDER}
    for r in rows:
        b = r.get("bucket")
        if b in dist:
            dist[b] += 1
    n = len(rows)
    dist_pct = {b: round(dist[b] / n * 100, 1) for b in REGIME_BUCKET_ORDER}

    bucket_col = "regime_bucket_csi800" if primary == "csi800" else "regime_bucket_csi300"
    raw_col = f"{bucket_col}_raw"
    cols = {r[1] for r in conn.execute("PRAGMA table_info(market_regime_daily)").fetchall()}
    raw_rows: list[dict[str, Any]] = []
    dist_raw: dict[str, int] = {b: 0 for b in REGIME_BUCKET_ORDER}
    if raw_col in cols:
        raw_db = conn.execute(
            f"""SELECT trade_date, {raw_col} AS bucket
                FROM market_regime_daily
                ORDER BY trade_date DESC LIMIT ?""",
            (max(30, min(days, 730)),),
        ).fetchall()
        for td, bucket in reversed(raw_db):
            b = bucket or "oscillation"
            if b not in dist_raw:
                b = "oscillation"
            dist_raw[b] += 1
            raw_rows.append({"trade_date": td, "bucket": b})
    dist_raw_pct = {b: round(dist_raw[b] / max(n, 1) * 100, 1) for b in REGIME_BUCKET_ORDER}

    return {
        "primary": primary,
        "primary_label": "中证800" if primary == "csi800" else "沪深300",
        "sample_days": n,
        "start_date": rows[0]["trade_date"],
        "end_date": rows[-1]["trade_date"],
        "bucket_order": list(REGIME_BUCKET_ORDER),
        "bucket_labels": {b: regime_bucket_label(b) for b in REGIME_BUCKET_ORDER},
        "distribution": dist,
        "distribution_pct": dist_pct,
        "distribution_raw": dist_raw if raw_rows else None,
        "distribution_raw_pct": dist_raw_pct if raw_rows else None,
        "persistence_min_days": config.REGIME_PERSISTENCE_DAYS,
        "persistence_asymmetric": config.REGIME_ASYMMETRIC_PERSISTENCE,
        "persistence_confirm_days": {
            "trend_up": config.REGIME_UP_CONFIRM_DAYS,
            "trend_down": config.REGIME_DOWN_CONFIRM_DAYS,
            "high_vol": config.REGIME_VOL_CONFIRM_DAYS,
            "oscillation": config.REGIME_OSC_CONFIRM_DAYS,
        } if config.REGIME_ASYMMETRIC_PERSISTENCE else None,
        "segments": _build_regime_segments(rows),
        "segments_raw": _build_regime_segments(raw_rows) if raw_rows else None,
        "daily": [
            {
                "trade_date": r["trade_date"],
                "bucket": r["bucket"],
                "bucket_label": regime_bucket_label(r["bucket"]),
                "regime_label": r.get("regime_csi800_label") or r.get("regime_label"),
                "volatility_20": r.get("volatility_20"),
                "price_vs_ma60": r.get("price_vs_ma60_csi800") if primary == "csi800" else r.get("price_vs_ma60"),
            }
            for r in rows
        ],
    }


def recompute_regime_buckets(conn: sqlite3.Connection) -> dict[str, Any]:
    """从已存储的七格 regime 重算四格 bucket（规则升级后无需重拉 K 线）。"""
    rows = conn.execute(
        """SELECT trade_date, regime, regime_csi800, price_vs_ma60, price_vs_ma60_csi800
           FROM market_regime_daily ORDER BY trade_date"""
    ).fetchall()
    updated = 0
    bucket_agree = 0
    dist300: dict[str, int] = {}
    dist800: dict[str, int] = {}
    for trade_date, regime, regime_csi800, pv300, pv800 in rows:
        b300 = regime_bucket(str(regime or "oscillation"))
        r800 = regime_csi800 or regime or "oscillation"
        b800 = regime_bucket(str(r800))
        dist300[b300] = dist300.get(b300, 0) + 1
        dist800[b800] = dist800.get(b800, 0) + 1
        agree = 1 if b300 == b800 else 0
        bucket_agree += agree
        conn.execute(
            """UPDATE market_regime_daily
               SET regime_bucket_csi300=?, regime_bucket_csi800=?,
                   regime_bucket_agreement=?, updated_at=datetime('now')
               WHERE trade_date=?""",
            (b300, b800, agree, trade_date),
        )
        updated += 1
    conn.commit()
    n = max(updated, 1)
    return {
        "updated_rows": updated,
        "bucket_distribution_csi300": dist300,
        "bucket_distribution_csi800": dist800,
        "bucket_agreement_pct": round(bucket_agree / n * 100, 1),
    }
