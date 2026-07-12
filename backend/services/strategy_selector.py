"""统一选股 — 回测与模拟盘共用 Top N 逻辑。"""
from __future__ import annotations

import math
import sqlite3
from typing import List, Optional

from config import DB_PATH
from services.strategy_registry import (
    effective_v5_strategy,
    get_meta,
    is_factor_id,
    normalize_strategy_id,
)
from services.strategies.sector_rotation_select import (
    sector_rotation_day_scores,
    select_sector_rotation,
)
from services.strategies.turtle import turtle_score
from services.strategy_types import SelectedStock
from services.v5_score_query import fetch_latest_top_n, resolve_score_spec


def _row_to_selected(r: sqlite3.Row | dict) -> SelectedStock:
    d = dict(r)
    return SelectedStock(
        stock_id=int(d["stock_id"]),
        code=str(d["code"]),
        name=str(d.get("name") or ""),
        score=float(d["score"]),
    )


def _select_v5(
    conn: sqlite3.Connection,
    strategy: str,
    min_score: float,
    top_n: int,
) -> List[SelectedStock]:
    spec = resolve_score_spec(effective_v5_strategy(strategy))
    if not spec:
        return []
    rows = fetch_latest_top_n(conn, spec, min_score, top_n)
    return [_row_to_selected(r) for r in rows]


def _momentum_score_from_quotes(rows: list) -> Optional[float]:
    """rows: DESC 序 (trade_date, close, volume)。"""
    if len(rows) < 12:
        return None
    rows = list(reversed(rows))
    closes = [float(r[1]) for r in rows if r[1]]
    volumes = [float(r[2] or 0) for r in rows]
    if len(closes) < 12:
        return None
    rets = [(closes[i] / closes[i - 1]) - 1 for i in range(1, len(closes))]
    momentum = sum(rets[-10:]) if rets else 0
    avg_r = sum(rets) / len(rets) if rets else 0
    vol = math.sqrt(sum((x - avg_r) ** 2 for x in rets) / len(rets)) if rets else 0
    low_vol = 1 / (vol + 0.001) if vol > 0 else 100
    if len(volumes) > 5 and sum(volumes) > 0:
        vols_avg = sum(volumes) / len(volumes)
        vol_stab = 1 / (abs(volumes[-1] / vols_avg - 1) + 0.1)
    else:
        vol_stab = 0
    return round(momentum * 0.4 * 100 + low_vol * 0.35 * 0.01 + vol_stab * 0.25 * 50 + 50, 2)


def _select_momentum(
    conn: sqlite3.Connection,
    min_score: float,
    top_n: int,
    lookback: int = 20,
) -> List[SelectedStock]:
    stocks = conn.execute(
        "SELECT id, code, name FROM stocks WHERE is_active=1"
    ).fetchall()
    scored: list[SelectedStock] = []
    need = lookback + 5
    for sid, code, name in stocks:
        qrows = conn.execute(
            """SELECT trade_date, close, volume FROM stock_daily_quotes
               WHERE stock_id=? AND close IS NOT NULL
               ORDER BY trade_date DESC LIMIT ?""",
            (sid, need),
        ).fetchall()
        sc = _momentum_score_from_quotes(qrows)
        if sc is not None and sc >= min_score:
            scored.append(SelectedStock(int(sid), code, name or "", sc))
    scored.sort(key=lambda x: -x.score)
    return scored[:top_n]


def _resolve_factor_id(strategy: str, combination_id: int | None) -> Optional[str]:
    key = normalize_strategy_id(strategy)
    meta = get_meta(key)
    if meta and meta.factor_id:
        return meta.factor_id
    if is_factor_id(key):
        return key.upper()
    if key == "factor_combination" or combination_id:
        if not combination_id:
            return None
        from services.factor_combinations import get_combination

        combo = get_combination(combination_id)
        if not combo:
            return None
        return combo.get("output_factor_id")
    return None


def _select_factor(
    conn: sqlite3.Connection,
    factor_id: str,
    min_score: float,
    top_n: int,
) -> List[SelectedStock]:
    latest = conn.execute(
        "SELECT MAX(date) FROM factor_values WHERE factor_id=?",
        (factor_id,),
    ).fetchone()
    if not latest or not latest[0]:
        return []
    dt = latest[0]
    rows = conn.execute(
        """SELECT s.id AS stock_id, s.code, s.name, fv.value AS score
           FROM factor_values fv
           JOIN stocks s ON fv.stock_id = s.id
           WHERE fv.factor_id=? AND fv.date=? AND s.is_active=1
             AND fv.value IS NOT NULL AND fv.value >= ?
           ORDER BY fv.value DESC LIMIT ?""",
        (factor_id, dt, min_score, top_n),
    ).fetchall()
    return [_row_to_selected(r) for r in rows]


def _turtle_live_score(
    conn: sqlite3.Connection,
    stock_id: int,
    entry: int = 20,
) -> Optional[float]:
    rows = conn.execute(
        """SELECT trade_date, close, high, low FROM stock_daily_quotes
           WHERE stock_id=? AND close IS NOT NULL
           ORDER BY trade_date DESC LIMIT ?""",
        (stock_id, entry + 2),
    ).fetchall()
    if len(rows) < entry + 1:
        return None
    rows = list(reversed(rows))
    dates = [str(r[0]) for r in rows]
    series = {
        str(r[0]): {
            "close": float(r[1]),
            "high": float(r[2] or r[1]),
            "low": float(r[3] or r[1]),
        }
        for r in rows
    }
    return turtle_score(series, dates, len(dates) - 1, entry=entry)


def _select_turtle(
    conn: sqlite3.Connection,
    min_score: float,
    top_n: int,
    lookback: int = 20,
) -> List[SelectedStock]:
    entry = max(10, lookback)
    stocks = conn.execute(
        "SELECT id, code, name FROM stocks WHERE is_active=1"
    ).fetchall()
    scored: list[SelectedStock] = []
    for sid, code, name in stocks:
        sc = _turtle_live_score(conn, int(sid), entry=entry)
        if sc is not None and sc >= min_score:
            scored.append(SelectedStock(int(sid), code, name or "", sc))
    scored.sort(key=lambda x: -x.score)
    return scored[:top_n]


def select_top_n(
    conn: sqlite3.Connection | None = None,
    *,
    strategy: str = "composite",
    top_n: int = 5,
    min_score: float = 50.0,
    combination_id: int | None = None,
    lookback: int = 20,
    sector_window: int = 5,
    per_sector: int = 2,
) -> tuple[List[SelectedStock], str | None]:
    """
    返回 (选股列表, error)。
    error 非空时列表为空。
    """
    external = conn is not None
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

    key = normalize_strategy_id(strategy)
    meta = get_meta(key)
    if not meta:
        if not external:
            conn.close()
        return [], f"未知策略: {strategy}"

    if meta.requires_combination_id and not combination_id:
        if not external:
            conn.close()
        return [], "factor_combination 需 combination_id"

    try:
        if meta.kind == "v5":
            rows = _select_v5(conn, key, min_score, top_n)
        elif meta.kind == "momentum":
            rows = _select_momentum(conn, min_score, top_n, lookback=lookback)
        elif meta.kind in ("factor", "combo"):
            fid = _resolve_factor_id(key, combination_id)
            if not fid:
                if not external:
                    conn.close()
                return [], "合成方案不存在或未 materialize"
            rows = _select_factor(conn, fid, min_score, top_n)
        elif meta.kind == "turtle":
            rows = _select_turtle(conn, min_score, top_n, lookback=lookback)
        elif meta.kind == "sector":
            rows, _, _, err = select_sector_rotation(
                conn,
                top_n=top_n,
                window_days=sector_window,
                per_sector=per_sector,
                min_score=min_score,
            )
            if err and not rows:
                if not external:
                    conn.close()
                return [], err
        else:
            rows = []
    finally:
        if not external:
            conn.close()

    strategy_key = meta.id
    if meta.kind == "combo" and combination_id:
        strategy_key = f"combo#{combination_id}"

    if not rows:
        return [], f"策略 {strategy_key} 无符合条件的股票"

    return rows, None


def nearest_score_snap(score_snap: dict, code: str, dt: str) -> Optional[float]:
    """回测：取不晚于 dt 的最近因子/V5 分。"""
    if code not in score_snap:
        return None
    best = None
    for sd in sorted(score_snap[code].keys()):
        if sd <= dt:
            best = sd
    if best is None and score_snap[code]:
        best = sorted(score_snap[code].keys())[0]
    return score_snap[code].get(best) if best else None


def momentum_backtest_score(
    series: dict,
    dates: list[str],
    idx: int,
    lookback: int,
) -> Optional[float]:
    """回测动量分 — 与模拟盘 _momentum_score_from_quotes 同公式。"""
    window = dates[max(0, idx - lookback) : idx + 1]
    closes = [float(series[d]["close"]) for d in window if d in series and series[d].get("close")]
    volumes = [float(series[d].get("volume") or 0) for d in window if d in series]
    if len(closes) < lookback * 0.6:
        return None
    rets = [(closes[i] / closes[i - 1]) - 1 for i in range(1, len(closes))]
    momentum = sum(rets[-10:]) if rets else 0
    avg_r = sum(rets) / len(rets) if rets else 0
    vol = math.sqrt(sum((x - avg_r) ** 2 for x in rets) / len(rets)) if rets else 0
    low_vol = 1 / (vol + 0.001) if vol > 0 else 100
    if len(volumes) > 5 and sum(volumes) > 0:
        vols_avg = sum(volumes) / len(volumes)
        vol_stab = 1 / (abs(volumes[-1] / vols_avg - 1) + 0.1)
    else:
        vol_stab = 0
    return round(momentum * 0.4 * 100 + low_vol * 0.35 * 0.01 + vol_stab * 0.25 * 50 + 50, 2)


def compute_backtest_day_scores(
    *,
    strategy: str,
    quotes: dict,
    dates: list[str],
    di: int,
    dt: str,
    available: dict[str, float],
    score_snap: dict | None = None,
    industry_map: dict[str, str] | None = None,
    lookback: int = 20,
    min_score: float = 50.0,
    sector_window: int = 5,
    use_factor_scores: bool = False,
) -> dict[str, float]:
    """回测单日选股得分 — 与 select_top_n 逻辑对齐。"""
    key = normalize_strategy_id(strategy)
    meta = get_meta(key)
    if not meta:
        return {}

    if meta.kind == "sector":
        return sector_rotation_day_scores(
            quotes,
            dates,
            di,
            industry_map or {},
            window=sector_window,
            min_score=min_score,
        )

    if meta.kind == "momentum":
        out: dict[str, float] = {}
        for code in available:
            sc = momentum_backtest_score(quotes.get(code, {}), dates, di, lookback)
            if sc is not None and sc >= min_score:
                out[code] = sc
        return out

    if meta.kind == "turtle":
        entry = max(10, lookback)
        out = {}
        for code in available:
            sc = turtle_score(quotes.get(code, {}), dates, di, entry=entry)
            if sc is not None and sc >= min_score:
                out[code] = sc
        return out

    if use_factor_scores and score_snap:
        out = {}
        for code in available:
            sc = nearest_score_snap(score_snap, code, dt)
            if sc is not None and sc >= min_score:
                out[code] = sc
        return out

    return {}


def select_top_n_dicts(**kwargs) -> tuple[list[dict], str | None]:
    rows, err = select_top_n(**kwargs)
    return [r.to_dict() for r in rows], err


def select_sector_rebalance(
    conn: sqlite3.Connection | None = None,
    *,
    top_n: int = 10,
    min_score: float = 0.0,
    sector_window: int = 5,
    per_sector: int = 2,
) -> tuple[list[dict], list[str], list[str], str | None]:
    """行业轮动调仓：买入列表 + 应卖代码 + 减仓行业。"""
    external = conn is not None
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
    try:
        rows, sell_codes, reduce_inds, err = select_sector_rotation(
            conn,
            top_n=top_n,
            window_days=sector_window,
            per_sector=per_sector,
            min_score=min_score,
        )
        if err and not rows:
            return [], sell_codes, reduce_inds, err
        return [r.to_dict() for r in rows], sell_codes, reduce_inds, None
    finally:
        if not external:
            conn.close()
