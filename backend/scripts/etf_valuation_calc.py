"""ETF 持仓加权估值(填宽基40%/主题10%的估值桩)。

Tushare 无指数估值接口、index_dailybasic 只覆盖5个老指数 → 走"持仓加权"路线:
每只股票 ETF 用前十大重仓(fund_portfolio,季度) × 成分股 PE(本地 valuation_snapshots)
加权 → ETF 篮子的加权 PE/PB。是横截面"贵不贵"(ETF 间比),非时序分位(需历史持仓,暂无)。

持仓按季度缓存到 etf_holdings(不每次调 Tushare);估值本地算,写 etf_valuation。
仅对有A股持仓的股票类(宽基/行业/主题/风格)。QDII/债/货/商无A股持仓→不算,估值权重自动重归一化。

用法: python scripts/etf_valuation_calc.py [--refresh-holdings]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

STOCK_CATS = ("宽基", "行业", "主题", "风格策略")


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS etf_holdings (
        code TEXT NOT NULL, end_date TEXT NOT NULL, symbol TEXT NOT NULL,
        ratio REAL, updated_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (code, end_date, symbol))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS etf_valuation (
        code TEXT PRIMARY KEY, end_date TEXT, calc_date TEXT,
        weighted_pe REAL, weighted_pb REAL, cov_ratio REAL,
        updated_at TEXT DEFAULT (datetime('now')))""")
    conn.commit()


def sync_holdings(conn: sqlite3.Connection, refresh: bool) -> int:
    """为股票类代表拉前十大持仓(季度缓存:已有≤100天的持仓则跳过,除非 --refresh-holdings)。"""
    from services.tushare_adapter import fetch_etf_portfolio_latest

    reps = conn.execute(
        "SELECT ts_code, code FROM etfs WHERE is_repr=1 AND category IN ({})".format(
            ",".join("?" * len(STOCK_CATS))), STOCK_CATS).fetchall()
    cutoff = (date.today() - timedelta(days=100)).strftime("%Y%m%d")
    fetched = 0
    for ts_code, code in reps:
        if not refresh:
            last = conn.execute("SELECT MAX(end_date) FROM etf_holdings WHERE code=?", (code,)).fetchone()[0]
            if last and last >= cutoff:
                continue  # 本季已缓存
        try:
            pf = fetch_etf_portfolio_latest(ts_code, top=15)
        except Exception:
            continue
        ed = pf.get("end_date")
        if not ed or not pf.get("holdings"):
            continue
        conn.executemany(
            "INSERT OR REPLACE INTO etf_holdings (code, end_date, symbol, ratio) VALUES (?,?,?,?)",
            [(code, ed, h["symbol"], h["ratio"]) for h in pf["holdings"] if h.get("symbol")],
        )
        conn.commit()
        fetched += 1
    return fetched


def calc_valuation(conn: sqlite3.Connection) -> int:
    """本地算加权 PE/PB:各 ETF 最新持仓期 × 成分股最新 PE(排除 None/≤0),权重归一。"""
    import pandas as pd

    val_date = conn.execute("SELECT MAX(as_of_date) FROM valuation_snapshots").fetchone()[0]
    # 成分股 code -> pe_ttm/pb
    stk = pd.read_sql_query(
        """SELECT s.code, v.pe_ttm, v.pb FROM stocks s
           JOIN valuation_snapshots v ON v.stock_id=s.id AND v.as_of_date=?""",
        conn, params=[val_date])
    pe_map = {r["code"]: r["pe_ttm"] for _, r in stk.iterrows()}
    pb_map = {r["code"]: r["pb"] for _, r in stk.iterrows()}

    # 每 ETF 最新持仓期
    hold = pd.read_sql_query(
        """SELECT h.code, h.symbol, h.ratio FROM etf_holdings h
           WHERE h.end_date=(SELECT MAX(end_date) FROM etf_holdings h2 WHERE h2.code=h.code)""",
        conn)
    n = 0
    today = date.today().strftime("%Y-%m-%d")
    for code, g in hold.groupby("code"):
        wpe = wpb = wsum = 0.0
        for _, h in g.iterrows():
            sc = str(h["symbol"]).split(".")[0]
            pe, pb, w = pe_map.get(sc), pb_map.get(sc), (h["ratio"] or 0)
            if w <= 0:
                continue
            if pe is not None and pe > 0:  # 排除亏损/无PE股,避免污染
                pe = min(pe, 150)  # 裁剪极端PE(个别微利股PE上千,防污染加权)
                wpe += pe * w; wsum += w
                if pb is not None and pb > 0:
                    wpb += pb * w
        if wsum <= 0:
            continue
        cov = wsum / (g["ratio"].fillna(0).sum() or 1)  # 有效PE权重占前十大权重比
        ed = conn.execute("SELECT MAX(end_date) FROM etf_holdings WHERE code=?", (code,)).fetchone()[0]
        conn.execute(
            """INSERT OR REPLACE INTO etf_valuation (code, end_date, calc_date, weighted_pe, weighted_pb, cov_ratio)
               VALUES (?,?,?,?,?,?)""",
            (code, ed, today, round(wpe / wsum, 3), round(wpb / wsum, 3) if wpb else None, round(cov, 3)))
        n += 1
    conn.commit()
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh-holdings", action="store_true", help="强制重拉持仓(否则季度缓存)")
    args = ap.parse_args()

    conn = sqlite3.connect(config.DB_PATH, timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    _ensure_tables(conn)
    nf = sync_holdings(conn, args.refresh_holdings)
    nv = calc_valuation(conn)
    print(f"持仓拉取={nf} 只  加权估值计算={nv} 只")
    import sqlite3 as _s
    sample = conn.execute("SELECT code, weighted_pe, cov_ratio FROM etf_valuation ORDER BY weighted_pe LIMIT 5").fetchall()
    print("  最便宜(加权PE最低)TOP5:", [(c, pe, cov) for c, pe, cov in sample])
    conn.close()


if __name__ == "__main__":
    main()
