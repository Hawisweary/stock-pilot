"""#60 Phase 2 — 日线行情 腾讯 vs Tushare shadow 对比(不切主源,只观测)。

比较口径:腾讯 close(前复权 qfq) vs Tushare adj_close(前复权 qfq)——两者都是前复权,理应吻合。
另报告 Tushare raw close 与 adj_close 的差(说明库内 close 列语义:腾讯=qfq / Tushare=raw)。

用法:
    python scripts/tushare_quote_shadow.py [--sample 40] [--days 60]

kill 线(见 docs/TUSHARE_PRIMARY_ASSESSMENT.md):
    adj_close 相对差 中位 < 0.1% 且 P95 < 0.5%,覆盖率 ≥ 腾讯的 99% → 允许切。
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import statistics
import sys

# 允许 `python scripts/xxx.py` 直接跑
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_env_token() -> None:
    """standalone 运行时 .env 不会自动注入,手动读 TUSHARE_TOKEN。"""
    if os.getenv("TUSHARE_TOKEN"):
        return
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env_path = os.path.join(root, ".env")
    if not os.path.isfile(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("TUSHARE_TOKEN=") and "=" in line:
                os.environ["TUSHARE_TOKEN"] = line.split("=", 1)[1].strip().strip('"').strip("'")
                break


def _rel_diff(a: float, b: float) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return abs(a - b) / abs(b)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=40, help="抽样股票数")
    ap.add_argument("--days", type=int, default=60, help="对比最近多少交易日")
    args = ap.parse_args()

    _load_env_token()
    from config import DB_PATH, TUSHARE_TOKEN
    if not TUSHARE_TOKEN:
        print("!! TUSHARE_TOKEN 未配置,无法对比"); return 2

    from services.tencent_adapter import fetch_daily_quotes
    from services.tushare_adapter import code_to_ts_code, fetch_daily_adjusted

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # 抽样:优先活跃、有行情的主板/创业(避开腾讯/Tushare 都可能缺的边角)
    stocks = conn.execute(
        """SELECT id, code, COALESCE(market,'A') market FROM stocks
           WHERE is_active=1 ORDER BY id LIMIT ?""",
        (args.sample * 3,),
    ).fetchall()
    conn.close()

    import random
    random.seed(42)
    stocks = random.sample(list(stocks), min(args.sample, len(stocks)))

    all_diffs: list[float] = []
    raw_vs_adj: list[float] = []
    covered = tencent_only = tushare_only = failed = 0
    per_stock: list[tuple[str, int, float | None]] = []

    for s in stocks:
        code, market = s["code"], s["market"]
        try:
            tdf = fetch_daily_quotes(code, market=market, count=args.days + 20)
            tmap = {}
            if tdf is not None and not tdf.empty:
                for idx, r in tdf.iterrows():
                    d = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
                    tmap[d] = float(r.get("close") or 0)

            ts_code = code_to_ts_code(code, market)
            # Tushare 起始日:取足够覆盖 days 个交易日(自然日 * 1.6)
            import datetime as dt
            end = dt.date.today().strftime("%Y%m%d")
            start = (dt.date.today() - dt.timedelta(days=int(args.days * 1.6) + 20)).strftime("%Y%m%d")
            trows = fetch_daily_adjusted(ts_code, start, end)
            umap = {r["trade_date"]: r for r in trows}

            common = sorted(set(tmap) & set(umap))[-args.days:]
            if not common:
                if tmap and not umap: tencent_only += 1
                elif umap and not tmap: tushare_only += 1
                else: failed += 1
                per_stock.append((code, 0, None)); continue

            covered += 1
            diffs = []
            for d in common:
                rd = _rel_diff(tmap[d], umap[d]["adj_close"])
                if rd is not None:
                    diffs.append(rd); all_diffs.append(rd)
                r = umap[d]
                rv = _rel_diff(r["close"], r["adj_close"])
                if rv is not None: raw_vs_adj.append(rv)
            per_stock.append((code, len(common), statistics.median(diffs) if diffs else None))
        except Exception as e:
            failed += 1
            per_stock.append((code, -1, None))
            print(f"  [{code}] 失败: {str(e)[:80]}")

    def pctl(xs: list[float], p: float) -> float:
        if not xs: return float("nan")
        xs = sorted(xs); k = min(len(xs) - 1, int(len(xs) * p))
        return xs[k]

    print("\n" + "=" * 60)
    print(f"日线行情 shadow 对比:腾讯 close(qfq) vs Tushare adj_close(qfq)")
    print(f"抽样 {len(stocks)} 股 · 每股最近 {args.days} 交易日")
    print("=" * 60)
    print(f"覆盖(两源都有可比日): {covered}/{len(stocks)}  ({100*covered/len(stocks):.0f}%)")
    print(f"仅腾讯有: {tencent_only} · 仅Tushare有: {tushare_only} · 失败: {failed}")
    if all_diffs:
        print(f"\n复权价相对差 |腾讯qfq - Tushare adj| / adj  (n={len(all_diffs)} 个日点):")
        print(f"  中位: {statistics.median(all_diffs)*100:.4f}%")
        print(f"  P95 : {pctl(all_diffs,0.95)*100:.4f}%")
        print(f"  P99 : {pctl(all_diffs,0.99)*100:.4f}%")
        print(f"  最大: {max(all_diffs)*100:.4f}%")
        med, p95 = statistics.median(all_diffs), pctl(all_diffs, 0.95)
        cov_ok = covered / len(stocks) >= 0.99
        kill_ok = med < 0.001 and p95 < 0.005 and cov_ok
        print(f"\n  kill线(中位<0.1% & P95<0.5% & 覆盖≥99%): "
              f"{'✅ 通过,可进 Phase 3 灰度切换' if kill_ok else '❌ 未过,主源留腾讯'}")
        print(f"    中位<0.1%: {'✅' if med<0.001 else '❌'} "
              f"P95<0.5%: {'✅' if p95<0.005 else '❌'} 覆盖≥99%: {'✅' if cov_ok else '❌'}")
    if raw_vs_adj:
        print(f"\n[口径提示] Tushare raw close vs adj_close 相对差 中位 "
              f"{statistics.median(raw_vs_adj)*100:.2f}% —— "
              f"说明库内 close 列语义:腾讯行=qfq,Tushare行=raw,切换需对齐下游读 close 的地方。")
    # 差异最大的几只
    bad = [(c, m) for c, n, m in per_stock if m is not None and m > 0.005]
    if bad:
        print(f"\n复权差 >0.5% 的股票({len(bad)}只):", ", ".join(f"{c}({m*100:.2f}%)" for c, m in bad[:15]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
