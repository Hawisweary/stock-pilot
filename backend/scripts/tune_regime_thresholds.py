#!/usr/bin/env python3
"""对比 L1 高波动阈值方案（252 日 CSI800）。

修复 K 线截断 bug 后，用正确 per-day 波动率重算各方案的四格分布，
帮助选择 vol_high / vol_expansion 参数。

用法:
  cd backend && python scripts/tune_regime_thresholds.py
  cd backend && python scripts/tune_regime_thresholds.py --days 252 --apply
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from services.market_regime import (
    RegimeThresholds,
    _prepare_kline,
    _rsi,
    _volatility,
    _adx,
    _pct_ret,
    _ma,
    _ma20_slope,
    classify_regime_state,
    default_regime_thresholds,
    regime_bucket,
    regime_bucket_label,
    sync_regime,
)


def _load_day_metrics(conn: sqlite3.Connection, dates: list[str], index_code: str):
    kline, _, err = _prepare_kline(index_code, days=config.REGIME_KLINE_DAYS)
    if err or not kline:
        raise RuntimeError(err or "无指数 K 线")
    by_date = {b["date"]: i for i, b in enumerate(kline)}

    feat_rows = {
        r[0]: r[1:]
        for r in conn.execute(
            """SELECT trade_date, ad_ratio, amount_ratio_20, avg_corr_20, rotation_speed
               FROM market_regime_daily WHERE trade_date IN ({})""".format(
                ",".join("?" * len(dates))
            ),
            tuple(dates),
        ).fetchall()
    }

    metrics: list[dict] = []
    skipped = 0
    for d in dates:
        idx = by_date.get(d)
        if idx is None or idx < 64:
            skipped += 1
            continue
        sub = kline[: idx + 1]
        closes = [float(b["close"]) for b in sub]
        highs = [float(b["high"]) for b in sub]
        lows = [float(b["low"]) for b in sub]
        fr = feat_rows.get(d)
        features = None
        if fr:
            features = {
                "ad_ratio": fr[0],
                "amount_ratio_20": fr[1],
                "avg_corr_20": fr[2],
                "rotation_speed": fr[3],
            }
        metrics.append(
            {
                "trade_date": d,
                "rsi": _rsi(closes, 14),
                "vol": _volatility(closes, 20),
                "vol60": _volatility(closes, 60) if len(closes) >= 61 else _volatility(closes, 20),
                "adx": _adx(highs, lows, closes, 14),
                "ret20": _pct_ret(closes, 20),
                "ret60": _pct_ret(closes, 60),
                "pvm20": (closes[-1] / _ma(closes, 20) - 1) if _ma(closes, 20) > 0 else 0.0,
                "pvm60": (closes[-1] / _ma(closes, 60) - 1) if _ma(closes, 60) > 0 else 0.0,
                "slope": _ma20_slope(closes),
                "features": features,
            }
        )
    return metrics, skipped


def _simulate(metrics: list[dict], thresholds: RegimeThresholds) -> tuple[Counter, Counter]:
    c7: Counter = Counter()
    c4: Counter = Counter()
    for m in metrics:
        regime = classify_regime_state(
            rsi=m["rsi"],
            vol=m["vol"],
            vol60=m["vol60"],
            adx=m["adx"],
            ret20=m["ret20"],
            ret60=m["ret60"],
            price_vs_ma20=m["pvm20"],
            price_vs_ma60=m["pvm60"],
            ma20_slope=m["slope"],
            features=m["features"],
            thresholds=thresholds,
        )
        c7[regime] += 1
        c4[regime_bucket(regime)] += 1
    return c7, c4


def _score(c4: Counter, n: int) -> float:
    """越接近 45% high_vol 且四格均有一定样本，得分越高。"""
    hv_pct = c4.get("high_vol", 0) / max(n, 1) * 100
    target_penalty = abs(hv_pct - 45.0)
    min_bucket = min(c4.get(b, 0) for b in ("trend_up", "high_vol", "oscillation", "trend_down"))
    sparse_penalty = max(0, 20 - min_bucket) * 2
    return 100 - target_penalty - sparse_penalty


def main() -> None:
    parser = argparse.ArgumentParser(description="L1 高波动阈值对比")
    parser.add_argument("--days", type=int, default=252)
    parser.add_argument("--apply", action="store_true", help="用当前 config 阈值回填 regime")
    parser.add_argument("--index", default=config.REGIME_INDEX_CSI800)
    args = parser.parse_args()

    conn = sqlite3.connect(config.DB_PATH, timeout=120)
    dates = [
        r[0]
        for r in conn.execute(
            """SELECT trade_date FROM market_regime_daily
               ORDER BY trade_date DESC LIMIT ?""",
            (args.days,),
        ).fetchall()
    ]
    dates = sorted(dates)
    if not dates:
        print("market_regime_daily 无数据，请先 backfill_regime_dual.py")
        sys.exit(1)

    metrics, skipped = _load_day_metrics(conn, dates, args.index)
    n = len(metrics)
    print(f"样本 {n} 天（跳过 {skipped}）| 指数 {args.index}")
    print(f"当前 config: vol_high={config.REGIME_VOL_HIGH} expansion={config.REGIME_VOL_EXPANSION}\n")

    scenarios: list[tuple[str, RegimeThresholds]] = []
    base = default_regime_thresholds()
    for vol in (0.25, 0.28, 0.30, 0.32):
        scenarios.append((f"A vol>{vol:.2f}", replace(base, vol_high=vol, vol_expansion=False)))
    for vol in (0.16, 0.17, 0.18, 0.20, 0.25, 0.28, 0.30):
        scenarios.append((f"B vol>{vol:.2f}+exp", replace(base, vol_high=vol, vol_expansion=True)))
    scenarios.append(("config default", base))

    rows_out: list[tuple[str, Counter, Counter, float]] = []
    print(f"{'方案':<22} {'高波动%':>8}  {'四格分布'}")
    print("-" * 72)
    for name, th in scenarios:
        c7, c4 = _simulate(metrics, th)
        hv = c4.get("high_vol", 0)
        pct = hv / n * 100
        dist = ", ".join(f"{regime_bucket_label(b)}={c4.get(b, 0)}" for b in ("trend_up", "high_vol", "oscillation", "trend_down"))
        sc = _score(c4, n)
        rows_out.append((name, c7, c4, sc))
        print(f"{name:<22} {pct:7.1f}%  {dist}")

    best = max(rows_out, key=lambda x: x[3])
    print("\n推荐方案:", best[0], f"(score={best[3]:.1f})")
    print("七格:", dict(best[1]))

    if args.apply:
        print("\n回填 market_regime_daily …")
        ok = err = 0
        for d in dates:
            try:
                r = sync_regime(conn, trade_date=d)
                if r.get("error") and not r.get("trade_date"):
                    err += 1
                else:
                    ok += 1
            except Exception:
                err += 1
        print(f"完成 ok={ok} err={err}")
        c4 = Counter(
            r[0]
            for r in conn.execute(
                """SELECT regime_bucket_csi800 FROM market_regime_daily
                   ORDER BY trade_date DESC LIMIT ?""",
                (args.days,),
            ).fetchall()
        )
        print("回填后 CSI800 四格:", dict(c4))

    conn.close()


if __name__ == "__main__":
    main()
