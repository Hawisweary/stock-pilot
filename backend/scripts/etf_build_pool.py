"""ETF 池清洗（基金经理框架第一步）：一级分类 + 同类内相关性聚类去重。

流程:
1. 给 etfs 表加列 category / cluster_id / is_repr / avg_amt_20d。
2. 按名称一级分类(services.etf_classify)。
3. 每个类别内:用近 N 日收益率序列贪心聚类——按流动性(20日均额)从高到低,
   若与已有代表相关性≥阈值则并入其簇(非代表),否则自成新簇(代表)。
   → 高度撞款的品种只留流动性最好的一只做代表,几百只压到几十只可比标的。
4. 写回 category / cluster_id / is_repr / avg_amt_20d。

用法: python scripts/etf_build_pool.py [--corr 0.95] [--min-obs 40]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from services.etf_classify import classify_etf


def _ensure_cols(conn: sqlite3.Connection) -> None:
    for col, decl in [
        ("category", "TEXT"),
        ("cluster_id", "INTEGER"),
        ("is_repr", "INTEGER DEFAULT 0"),
        ("avg_amt_20d", "REAL"),
    ]:
        try:
            conn.execute(f"ALTER TABLE etfs ADD COLUMN {col} {decl}")
        except sqlite3.OperationalError:
            pass  # 已存在
    conn.commit()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corr", type=float, default=0.95, help="同簇相关性阈值(默认0.95)")
    ap.add_argument("--min-obs", type=int, default=40, help="参与聚类的最少收益率观测数")
    args = ap.parse_args()

    import numpy as np
    import pandas as pd

    conn = sqlite3.connect(config.DB_PATH, timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    _ensure_cols(conn)

    # 1) 分类
    etfs = conn.execute("SELECT ts_code, name FROM etfs WHERE is_active=1").fetchall()
    cat_map = {ts: classify_etf(name) for ts, name in etfs}

    # 2) 行情 → 收盘价透视表 + 收益率 + 20日均额
    q = pd.read_sql_query(
        "SELECT ts_code, trade_date, close, amount FROM etf_daily_quotes", conn
    )
    if q.empty:
        print("etf_daily_quotes 为空，先回填行情"); return
    close = q.pivot(index="trade_date", columns="ts_code", values="close").sort_index()
    rets = close.pct_change()
    n_days = len(close)
    amt = q.pivot(index="trade_date", columns="ts_code", values="amount").sort_index()
    avg_amt_20d = amt.tail(20).mean()  # 千元

    # 3) 每类别贪心聚类
    cluster_of: dict[str, int] = {}
    is_repr: dict[str, int] = {}
    next_cid = 1
    cats: dict[str, list[str]] = {}
    for ts, cat in cat_map.items():
        cats.setdefault(cat, []).append(ts)

    summary = {}
    for cat, codes in cats.items():
        # 有足够历史的参与聚类，按流动性降序
        elig = [c for c in codes if c in rets.columns and rets[c].notna().sum() >= args.min_obs]
        elig.sort(key=lambda c: (avg_amt_20d.get(c) if not pd.isna(avg_amt_20d.get(c, np.nan)) else 0), reverse=True)
        reps: list[str] = []
        rep_cid: dict[str, int] = {}
        for c in elig:
            matched = None
            for rep in reps:
                corr = rets[c].corr(rets[rep])
                if corr is not None and not np.isnan(corr) and corr >= args.corr:
                    matched = rep
                    break
            if matched is None:
                nonlocal_cid = next_cid
                reps.append(c)
                rep_cid[c] = nonlocal_cid
                cluster_of[c] = nonlocal_cid
                is_repr[c] = 1
                next_cid += 1
            else:
                cluster_of[c] = rep_cid[matched]
                is_repr[c] = 0
        summary[cat] = (len(codes), len(elig), len(reps))

    # 4) 写回
    for ts, cat in cat_map.items():
        conn.execute(
            "UPDATE etfs SET category=?, cluster_id=?, is_repr=?, avg_amt_20d=? WHERE ts_code=?",
            (cat, cluster_of.get(ts), is_repr.get(ts, 0),
             float(avg_amt_20d.get(ts)) if ts in avg_amt_20d and not pd.isna(avg_amt_20d.get(ts)) else None,
             ts),
        )
    conn.commit()

    print(f"行情天数={n_days}  相关阈值={args.corr}  最少观测={args.min_obs}")
    print(f"{'类别':<8}{'总数':>6}{'入选聚类':>8}{'代表数':>8}  (压缩)")
    tot_c = tot_r = 0
    for cat in ["宽基", "行业", "主题", "风格策略", "QDII", "债券货币", "商品", "其他"]:
        if cat not in summary:
            continue
        total, elig_n, reps_n = summary[cat]
        tot_c += total; tot_r += reps_n
        print(f"{cat:<8}{total:>6}{elig_n:>8}{reps_n:>8}")
    print(f"{'合计':<8}{tot_c:>6}{'':>8}{tot_r:>8}  → 全市场 {tot_c} 只压到 {tot_r} 只可比代表")
    conn.close()


if __name__ == "__main__":
    main()
