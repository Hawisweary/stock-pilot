"""ETF 资金流计算(打分体系的地基)。

真资金流 = 份额变动 × 昨日净值(聪明钱真实进出),≠ 成交额。
从 etf_daily_quotes(fd_share 份额/万份, unit_nav 净值/元)算:
  单日净流入(万元) = (今日份额 - 昨日份额) × 昨日净值
  → 滚动 5/20/60 日累计;强度 = 20日累计/最新规模(跨品种可比);
    动量 = (20日 - 60日累计)/|60日累计|(资金加速)
顺带算 规模(亿元) = 最新份额 × 最新净值 —— 同时填了打分体系的"规模"桩。
结果写 etf_flow(每 ETF 最新交易日一行,供周度打分关联)。

用法: python scripts/etf_flow_calc.py [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS etf_flow (
        code TEXT NOT NULL, trade_date TEXT NOT NULL,
        fd_share REAL, unit_nav REAL, fund_size REAL,   -- 规模:亿元
        daily_net_flow REAL, flow_5d REAL, flow_20d REAL, flow_60d REAL,
        flow_intensity REAL, flow_momentum REAL,
        updated_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (code, trade_date))""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_etf_flow_date ON etf_flow(trade_date)")
    conn.commit()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="计算日(默认最新有份额的交易日)")
    args = ap.parse_args()

    import numpy as np
    import pandas as pd

    conn = sqlite3.connect(config.DB_PATH, timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    _ensure_table(conn)

    df = pd.read_sql_query(
        """SELECT q.ts_code, e.code, q.trade_date, q.fd_share, q.unit_nav
           FROM etf_daily_quotes q JOIN etfs e ON e.ts_code=q.ts_code
           WHERE q.fd_share IS NOT NULL AND q.unit_nav IS NOT NULL""",
        conn,
    )
    if df.empty:
        print("无 fd_share/unit_nav 数据(先跑 tushare_sync_etf --flow-days N)"); return

    df = df.sort_values(["ts_code", "trade_date"])
    g = df.groupby("ts_code", group_keys=False)
    df["share_lag1"] = g["fd_share"].shift(1)
    df["nav_lag1"] = g["unit_nav"].shift(1)
    # 单日净流入(万元) = Δ份额(万份) × 昨日净值(元)
    df["daily_net_flow"] = ((df["fd_share"] - df["share_lag1"]) * df["nav_lag1"]).fillna(0.0)
    for w in (5, 20, 60):
        df[f"flow_{w}d"] = df.groupby("ts_code")["daily_net_flow"].transform(
            lambda x: x.rolling(w, min_periods=1).sum()
        )
    # 规模(亿元) = 份额(万份) × 净值(元) / 1e4
    df["fund_size"] = df["fd_share"] * df["unit_nav"] / 1e4
    df["flow_intensity"] = df["flow_20d"] / (df["fund_size"] * 1e4)  # 20日万元 / 规模万元
    df["flow_momentum"] = np.where(
        df["flow_60d"].abs() > 1e-9, (df["flow_20d"] - df["flow_60d"]) / df["flow_60d"].abs(), np.nan
    )

    # 每 ETF 取计算日(或最新)那一行落库
    target = args.date
    latest = df.groupby("ts_code").tail(1) if target is None else df[df["trade_date"] == target]
    if target is not None and latest.empty:
        latest = df.groupby("ts_code").tail(1)  # 该日无数据则回退最新

    n = 0
    for _, r in latest.iterrows():
        conn.execute(
            """INSERT OR REPLACE INTO etf_flow
               (code, trade_date, fd_share, unit_nav, fund_size, daily_net_flow,
                flow_5d, flow_20d, flow_60d, flow_intensity, flow_momentum)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (r["code"], str(r["trade_date"]), _n(r["fd_share"]), _n(r["unit_nav"]),
             _n(r["fund_size"]), _n(r["daily_net_flow"]), _n(r["flow_5d"]), _n(r["flow_20d"]),
             _n(r["flow_60d"]), _n(r["flow_intensity"]), _n(r["flow_momentum"])),
        )
        n += 1
    conn.commit()
    d0 = latest["trade_date"].max()
    inflow = int((latest["flow_20d"] > 0).sum())
    print(f"资金流计算完成 日={d0} ETF={n}  近20日净流入为正={inflow} / 净流出={n - inflow}")
    top = latest.nlargest(5, "flow_20d")[["code", "flow_20d", "fund_size"]]
    print("  20日净流入 TOP5(万元):", [(r["code"], round(r["flow_20d"]), round(r["fund_size"], 1)) for _, r in top.iterrows()])
    conn.close()


def _n(v):
    import pandas as pd
    return None if v is None or pd.isna(v) else float(v)


if __name__ == "__main__":
    main()
