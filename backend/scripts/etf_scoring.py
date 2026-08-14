"""ETF 分层打分(基金经理框架第二步)。

只对池清洗后的代表 ETF(is_repr=1)打分,同类内可比。
v1 实打实算的指标:动量(1/3/6月收益+夏普+年化波动)、流动性(20日均额)、费率。
留桩指标(需新数据管线,当前为 None、0 权重,管线上线后自动进入合成):
  规模 / 折溢价 / 跟踪误差 / 估值PE分位 / 资金流 / 行业景气度 / 策略纯度。

打分流程:同类内 winsorize+Z-Score 标准化 → 按类别权重(仅在有值的指标上重归一化)
加权求和 → 同类内排名。结果写 etf_score 周度快照(便于回测/归因)。

用法: python scripts/etf_scoring.py [--date YYYY-MM-DD] [--corr-note]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

# 类别权重(对齐框架)。键=指标名,值=权重。留桩指标也列出——有值才参与、并重归一化。
# 通用层(size/liquidity/tracking/fee/premium)+ 类别专属层(valuation/momentum/flow/boom/purity)
WEIGHTS: dict[str, dict[str, float]] = {
    "宽基":     {"size": .10, "liquidity": .10, "tracking": .05, "fee": .05, "premium": .05, "valuation": .40, "momentum": .25},
    "行业":     {"size": .05, "liquidity": .05, "tracking": .05, "fee": .05, "premium": .05, "momentum": .20, "flow": .30, "boom": .30},
    "主题":     {"size": .05, "liquidity": .05, "tracking": .05, "fee": .05, "premium": .05, "momentum": .30, "flow": .40, "valuation": .10},
    "风格策略": {"size": .05, "liquidity": .05, "tracking": .05, "fee": .05, "premium": .05, "purity": .50, "momentum": .25},
    "QDII":     {"size": .10, "liquidity": .15, "tracking": .10, "fee": .10, "premium": .10, "momentum": .45},
}
SCORED_CATEGORIES = list(WEIGHTS.keys())  # 债券货币/商品/其他 不参与股票框架打分


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS etf_score (
        code TEXT NOT NULL, trade_date TEXT NOT NULL,
        category TEXT, is_repr INTEGER DEFAULT 1,
        -- 原始指标
        avg_amt_20d REAL, fee_rate REAL,
        return_1m REAL, return_3m REAL, return_6m REAL, sharpe REAL, annual_vol REAL,
        -- 标准化分数(留桩列为 NULL)
        score_size REAL, score_liquidity REAL, score_tracking REAL, score_fee REAL,
        score_premium REAL, score_valuation REAL, score_momentum REAL, score_flow REAL,
        score_boom REAL, score_purity REAL,
        -- 输出
        composite_score REAL, rank_in_category INTEGER,
        created_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (code, trade_date))""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_etf_score_date_cat ON etf_score(trade_date, category)")
    # 资金流/规模原始列(管线上线后补,幂等)
    for col in ("fund_size", "net_inflow_20d"):
        try:
            conn.execute(f"ALTER TABLE etf_score ADD COLUMN {col} REAL")
        except sqlite3.OperationalError:
            pass
    conn.commit()


def _winsor_z(s):
    import numpy as np
    import pandas as pd

    x = pd.to_numeric(s, errors="coerce")
    if x.notna().sum() < 3:
        return pd.Series([np.nan] * len(x), index=x.index)
    lo, hi = x.quantile(0.01), x.quantile(0.99)
    x = x.clip(lo, hi)
    mu, sd = x.mean(), x.std()
    if not sd or np.isnan(sd):
        return pd.Series([0.0] * len(x), index=x.index)
    return (x - mu) / sd


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="评分日期(默认最新交易日)")
    args = ap.parse_args()

    import numpy as np
    import pandas as pd

    conn = sqlite3.connect(config.DB_PATH, timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    _ensure_table(conn)

    # 1) 代表 ETF(仅参与股票框架的类别)
    reps = pd.read_sql_query(
        "SELECT ts_code, code, category, avg_amt_20d, m_fee, c_fee FROM etfs "
        "WHERE is_repr=1 AND category IN ({})".format(",".join("?" * len(SCORED_CATEGORIES))),
        conn, params=SCORED_CATEGORIES,
    )
    if reps.empty:
        print("代表 ETF 为空(先跑 etf_build_pool)"); return

    # 资金流 + 规模(etf_flow 最新一期);无表时为空 → score_flow/score_size 自动留桩
    try:
        # 按 code 取各自最新一期(fund_share 披露滞后不一,不能用全局 MAX 过滤)
        flow = pd.read_sql_query(
            "SELECT f.code, f.fund_size, f.flow_20d, f.flow_intensity FROM etf_flow f "
            "WHERE f.trade_date=(SELECT MAX(trade_date) FROM etf_flow f2 WHERE f2.code=f.code)", conn)
    except Exception:
        flow = pd.DataFrame(columns=["code", "fund_size", "flow_20d", "flow_intensity"])

    # 2) 行情 → 动量指标
    q = pd.read_sql_query("SELECT ts_code, trade_date, close FROM etf_daily_quotes", conn)
    close = q.pivot(index="trade_date", columns="ts_code", values="close").sort_index()
    trade_date = args.date or close.index[-1]
    close = close.loc[:trade_date]  # 只用评分日及之前
    rets = close.pct_change()

    def mom(ts, ndays):
        if ts not in close.columns:
            return None
        s = close[ts].dropna()
        if len(s) < ndays + 1:
            return None
        return round((s.iloc[-1] / s.iloc[-ndays - 1] - 1) * 100, 3)

    def sharpe_vol(ts):
        if ts not in rets.columns:
            return None, None
        r = rets[ts].dropna()
        if len(r) < 20 or r.std() == 0:
            return None, None
        ann_ret = r.mean() * 252
        ann_vol = r.std() * np.sqrt(252)
        return round(ann_ret / ann_vol, 3), round(ann_vol * 100, 3)

    recs = []
    for _, e in reps.iterrows():
        ts = e["ts_code"]
        sh, vol = sharpe_vol(ts)
        fee = (e["m_fee"] or 0) + (e["c_fee"] or 0) if (e["m_fee"] is not None or e["c_fee"] is not None) else None
        recs.append({
            "code": e["code"], "category": e["category"],
            "avg_amt_20d": e["avg_amt_20d"], "fee_rate": fee,
            "return_1m": mom(ts, 21), "return_3m": mom(ts, 63), "return_6m": mom(ts, 120),
            "sharpe": sh, "annual_vol": vol,
        })
    df = pd.DataFrame(recs).merge(flow, on="code", how="left")

    # 3) 同类内标准化 → 分数
    for col in ("score_liquidity", "score_fee", "score_momentum", "score_flow", "score_size"):
        df[col] = np.nan
    for cat, g in df.groupby("category"):
        idx = g.index
        # 流动性:log(20日均额),越大越好
        df.loc[idx, "score_liquidity"] = _winsor_z(np.log(g["avg_amt_20d"].fillna(0) + 1)).values
        # 费率:越低越好(转向)
        df.loc[idx, "score_fee"] = (-_winsor_z(g["fee_rate"])).values
        # 动量:1/3/6月收益 + 夏普 各自 Z 后等权,再对合成值 Z 一次
        zr = pd.concat([_winsor_z(g["return_1m"]), _winsor_z(g["return_3m"]),
                        _winsor_z(g["return_6m"]), _winsor_z(g["sharpe"])], axis=1).mean(axis=1)
        df.loc[idx, "score_momentum"] = _winsor_z(zr).values
        # 资金流:强度(20日净流入/规模,跨大小可比),越大越好
        df.loc[idx, "score_flow"] = _winsor_z(g["flow_intensity"]).values
        # 规模:log(规模亿元),越大越好(清盘风险低/机构认可)
        df.loc[idx, "score_size"] = _winsor_z(np.log(g["fund_size"].clip(lower=0) + 1)).values

    # 仍留桩(需新管线,当前 None,上线后自动进入合成)
    for c in ["score_tracking", "score_premium", "score_valuation", "score_boom", "score_purity"]:
        df[c] = np.nan

    # 4) 类别权重加权(仅在有值的分数上重归一化)→ 合成 + 同类排名
    df["composite_score"] = np.nan
    df["rank_in_category"] = np.nan
    for cat, g in df.groupby("category"):
        w = WEIGHTS.get(cat, {})
        comp = pd.Series(0.0, index=g.index)
        for i in g.index:
            avail = {ind: w[ind] for ind in w if f"score_{ind}" in df.columns and not pd.isna(df.loc[i, f"score_{ind}"])}
            wsum = sum(avail.values())
            if wsum <= 0:
                comp[i] = np.nan; continue
            comp[i] = sum(df.loc[i, f"score_{ind}"] * (wt / wsum) for ind, wt in avail.items())
        df.loc[g.index, "composite_score"] = comp
        df.loc[g.index, "rank_in_category"] = comp.rank(ascending=False, method="min")

    # 5) 落库
    n = 0
    for _, r in df.iterrows():
        conn.execute(
            """INSERT OR REPLACE INTO etf_score
               (code, trade_date, category, is_repr, avg_amt_20d, fee_rate, fund_size, net_inflow_20d,
                return_1m, return_3m, return_6m, sharpe, annual_vol,
                score_liquidity, score_fee, score_momentum, score_flow, score_size,
                composite_score, rank_in_category)
               VALUES (?,?,?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r["code"], str(trade_date), r["category"], _n(r["avg_amt_20d"]), _n(r["fee_rate"]),
             _n(r.get("fund_size")), _n(r.get("flow_20d")),
             _n(r["return_1m"]), _n(r["return_3m"]), _n(r["return_6m"]), _n(r["sharpe"]), _n(r["annual_vol"]),
             _n(r["score_liquidity"]), _n(r["score_fee"]), _n(r["score_momentum"]),
             _n(r.get("score_flow")), _n(r.get("score_size")),
             _n(r["composite_score"]), int(r["rank_in_category"]) if not pd.isna(r["rank_in_category"]) else None),
        )
        n += 1
    conn.commit()

    print(f"评分日={trade_date}  打分ETF={n}(仅代表)")
    print(f"{'类别':<8}{'数量':>6}  各类 TOP3(综合分)")
    for cat in SCORED_CATEGORIES:
        g = df[df["category"] == cat].nsmallest(3, "rank_in_category")
        top = "  ".join(f"{r['code']}({r['composite_score']:.2f})" for _, r in g.iterrows())
        print(f"{cat:<8}{(df['category'] == cat).sum():>6}  {top}")
    conn.close()


def _n(v):
    import pandas as pd
    return None if v is None or pd.isna(v) else float(v)


if __name__ == "__main__":
    main()
