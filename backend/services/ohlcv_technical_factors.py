"""OHLCV 自算技术因子 — 动量/反转/波动/ADX/价量相关/振幅/WQ样例/融资变化"""
from __future__ import annotations

import math
from typing import Any, Callable, Optional


def _pct_momentum(closes: list[float], lag: int) -> Optional[float]:
    if len(closes) <= lag or closes[lag] <= 0:
        return None
    return round((closes[0] / closes[lag] - 1) * 100, 4)


def _reversal(closes: list[float], lag: int) -> Optional[float]:
    m = _pct_momentum(closes, lag)
    return round(-m, 4) if m is not None else None


def _volatility(closes: list[float], window: int) -> Optional[float]:
    if len(closes) <= window:
        return None
    rets = [
        (closes[i] / closes[i + 1] - 1)
        for i in range(min(window - 1, len(closes) - 1))
    ]
    if not rets:
        return None
    return round(-math.sqrt(sum(r * r for r in rets) / len(rets)) * 100, 4)


def _rolling_corr(xs: list[float], ys: list[float], window: int) -> Optional[float]:
    if len(xs) < window or len(ys) < window:
        return None
    x = xs[:window]
    y = ys[:window]
    mx = sum(x) / window
    my = sum(y) / window
    num = sum((x[i] - mx) * (y[i] - my) for i in range(window))
    den_x = math.sqrt(sum((v - mx) ** 2 for v in x))
    den_y = math.sqrt(sum((v - my) ** 2 for v in y))
    if den_x < 1e-12 or den_y < 1e-12:
        return None
    return round(num / (den_x * den_y), 4)


def _amplitude_std(
    highs: list[float], lows: list[float], closes: list[float], window: int
) -> Optional[float]:
    n = min(window, len(highs), len(lows), len(closes))
    if n < 10:
        return None
    amps = []
    for i in range(n):
        c = closes[i]
        if c and c > 0:
            amps.append((highs[i] - lows[i]) / c)
    if len(amps) < 10:
        return None
    mean = sum(amps) / len(amps)
    var = sum((a - mean) ** 2 for a in amps) / len(amps)
    return round(math.sqrt(var) * 100, 4)


def _adx(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> Optional[float]:
    n = len(closes)
    if n < period + 2:
        return None
    tr_list, plus_dm, minus_dm = [], [], []
    for i in range(1, n):
        h, l, pc = highs[i], lows[i], closes[i - 1]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        up = highs[i - 1] - h
        down = l - lows[i - 1]
        tr_list.append(tr)
        plus_dm.append(up if up > down and up > 0 else 0)
        minus_dm.append(down if down > up and down > 0 else 0)

    def _wilder(vals: list[float]) -> list[float]:
        out = [sum(vals[:period])]
        for v in vals[period:]:
            out.append(out[-1] - out[-1] / period + v)
        return out

    tr_s = _wilder(tr_list)
    pdm_s = _wilder(plus_dm)
    mdm_s = _wilder(minus_dm)
    dxs = []
    for i in range(len(tr_s)):
        if tr_s[i] <= 0:
            continue
        pdi = 100 * pdm_s[i] / tr_s[i]
        mdi = 100 * mdm_s[i] / tr_s[i]
        if pdi + mdi <= 0:
            continue
        dxs.append(100 * abs(pdi - mdi) / (pdi + mdi))
    if len(dxs) < period:
        return None
    adx = sum(dxs[-period:]) / period
    return round(adx, 4)


def _ma_crossover_filtered(q: dict) -> Optional[float]:
    """MA5/MA20 金叉 + ADX 门控 + 0.5% 迟滞；震荡市返回 0。"""
    closes = q.get("closes") or []
    highs = q.get("highs") or []
    lows = q.get("lows") or []
    if len(closes) < 20:
        return None
    ma5 = sum(closes[:5]) / 5
    ma20 = sum(closes[:20]) / 20
    adx = _adx(highs, lows, closes, 14)
    if adx is None or adx < 20:
        return 0.0
    if ma5 > ma20 * 1.005:
        return 1.0
    if ma5 < ma20 * 0.995:
        return -1.0
    return 0.0


def _wq_alpha6(closes: list[float], opens: list[float], volumes: list[float]) -> Optional[float]:
    """WQ Alpha#6 样例: -corr(open, volume, 10)"""
    if len(closes) < 11:
        return None
    v = _rolling_corr(opens[:10], volumes[:10], 10)
    return round(-v, 4) if v is not None else None


def _wq_alpha12(closes: list[float], volumes: list[float]) -> Optional[float]:
    """WQ Alpha#12 样例: sign(delta(volume,1)) * (-delta(close,1))"""
    if len(closes) < 3 or len(volumes) < 3:
        return None
    dv = volumes[0] - volumes[1]
    dc = closes[0] - closes[1]
    sign = 1.0 if dv > 0 else (-1.0 if dv < 0 else 0.0)
    return round(sign * (-dc), 4)


def _turnover_adj(turnovers: list[float]) -> Optional[float]:
    vals = [t for t in turnovers[:20] if t and t > 0]
    if len(vals) < 5:
        return None
    avg = sum(vals) / len(vals)
    if avg <= 0:
        return None
    return round(vals[0] / avg, 4)


def _margin_change(margins: list[float], lag: int) -> Optional[float]:
    if len(margins) <= lag or margins[lag] <= 0:
        return None
    return round((margins[0] / margins[lag] - 1) * 100, 4)


# factor_id -> (name, category, compute_fn needs quote rows dict)
OHLCV_FACTOR_SPECS: list[tuple[str, str, str, Callable[[dict], Optional[float]]]] = [
    ("F009", "momentum_20d", "动量", lambda q: _pct_momentum(q["closes"], 20)),
    ("F010", "volatility_20d", "低波", lambda q: _volatility(q["closes"], 20)),
    ("F011", "volume_ratio", "量价", lambda q: (
        round((sum(q["vols"][:5]) / 5) / (sum(q["vols"][:20]) / 20), 4)
        if len(q["vols"]) >= 20 and sum(q["vols"][:20]) > 0 else None
    )),
    ("F016", "momentum_60d", "动量", lambda q: _pct_momentum(q["closes"], 60)),
    ("F017", "momentum_120d", "动量", lambda q: _pct_momentum(q["closes"], 120)),
    ("F018", "momentum_250d", "动量", lambda q: _pct_momentum(q["closes"], 250)),
    ("F019", "reversal_5d", "反转", lambda q: _reversal(q["closes"], 5)),
    ("F020", "reversal_20d", "反转", lambda q: _reversal(q["closes"], 20)),
    ("F021", "volatility_5d", "低波", lambda q: _volatility(q["closes"], 5)),
    ("F022", "volatility_60d", "低波", lambda q: _volatility(q["closes"], 60)),
    ("F023", "adx_14", "趋势", lambda q: _adx(q["highs"], q["lows"], q["closes"], 14)),
    ("F024", "pv_corr_5d", "量价", lambda q: _rolling_corr(q["closes"], q["vols"], 5)),
    ("F025", "pv_corr_20d", "量价", lambda q: _rolling_corr(q["closes"], q["vols"], 20)),
    ("F026", "amplitude_std_120d", "波动", lambda q: _amplitude_std(q["highs"], q["lows"], q["closes"], 120)),
    ("F027", "wq_alpha6", "WQ", lambda q: _wq_alpha6(q["closes"], q["opens"], q["vols"])),
    ("F028", "wq_alpha12", "WQ", lambda q: _wq_alpha12(q["closes"], q["vols"])),
    ("F029", "margin_chg_5d", "融资", lambda q: _margin_change(q.get("margins", []), 5)),
    ("F030", "margin_chg_20d", "融资", lambda q: _margin_change(q.get("margins", []), 20)),
    ("F031", "ma_crossover_filtered", "趋势", _ma_crossover_filtered),
]

NEUTRALIZE_SOURCE_IDS = [
    "F009", "F010", "F016", "F017", "F018", "F019", "F020",
    "F021", "F022", "F024", "F025", "F026", "F029", "F030",
]


def load_quote_panel(conn, stock_id: int, _code: str, as_of: str, *, lookback: int = 260) -> dict[str, Any]:
    sql_full = """SELECT trade_date,
                         COALESCE(adj_close, close) AS px,
                         open, high, low, volume, turnover
                  FROM stock_daily_quotes
                  WHERE stock_id=? AND trade_date <= ?
                    AND COALESCE(adj_close, close) IS NOT NULL
                  ORDER BY trade_date DESC LIMIT ?"""
    sql_min = """SELECT trade_date,
                        COALESCE(adj_close, close) AS px,
                        COALESCE(adj_close, close) AS open,
                        COALESCE(adj_close, close) AS high,
                        COALESCE(adj_close, close) AS low,
                        volume, NULL AS turnover
                 FROM stock_daily_quotes
                 WHERE stock_id=? AND trade_date <= ?
                   AND COALESCE(adj_close, close) IS NOT NULL
                 ORDER BY trade_date DESC LIMIT ?"""
    try:
        rows = conn.execute(sql_full, (stock_id, as_of, lookback)).fetchall()
    except Exception:
        rows = conn.execute(sql_min, (stock_id, as_of, lookback)).fetchall()
    margins: list[float] = []
    try:
        margins = [
            r[0]
            for r in conn.execute(
                """SELECT margin_balance FROM eastmoney_margin
                   WHERE stock_id=? AND date <= ? ORDER BY date DESC LIMIT ?""",
                (stock_id, as_of, lookback),
            ).fetchall()
        ]
    except Exception:
        margins = []
    return {
        "closes": [r[1] for r in rows],
        "opens": [r[2] or r[1] for r in rows],
        "highs": [r[3] or r[1] for r in rows],
        "lows": [r[4] or r[1] for r in rows],
        "vols": [r[5] or 0 for r in rows],
        "turnovers": [r[6] for r in rows],
        "margins": margins,
        "n_bars": len(rows),
    }


def compute_ohlcv_factors(panel: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    if panel["n_bars"] < 21:
        return out
    for fid, _name, _cat, fn in OHLCV_FACTOR_SPECS:
        try:
            val = fn(panel)
            if val is not None and math.isfinite(val):
                out[fid] = float(val)
        except Exception:
            continue
    ta = _turnover_adj(panel["turnovers"])
    if ta is not None:
        out["F014"] = ta
    return out
