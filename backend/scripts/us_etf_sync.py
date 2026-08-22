"""美股 ETF 同步:精选流动性 universe(主流 ~130 只)。

- us_etfs:元信息(名称/类别/家族/规模/费率)—— 列表页用
- us_etf_profile:完整 profile JSON(持仓/板块/配置/简介)—— 详情页缓存
- 行情写入共享 us_daily_quotes(与个股同表,K线/复权端点直接复用)
独立于个股(us_stocks),不参与打分。冷门 ETF 走详情页懒加载,不在此列。
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from services.us_market_adapter import fetch_us_etf_profile, fetch_us_quotes_bulk

CURATED = [
    # 宽基
    "SPY","VOO","IVV","VTI","QQQ","QQQM","DIA","IWM","IWB","RSP","ITOT","SPLG","VT","ACWI",
    # 风格/红利
    "IWF","IWD","VUG","VTV","SCHG","SCHD","MGK","VYM","VIG","DGRO","NOBL","HDV","MOAT",
    "QUAL","MTUM","USMV","VLUE","SPHD","DVY",
    # SPDR 行业
    "XLK","XLF","XLV","XLE","XLY","XLP","XLI","XLU","XLRE","XLC","XLB",
    # 行业/主题
    "VGT","VHT","VNQ","SMH","SOXX","IGV","SKYY","FDN","XBI","IBB","IHI","ITB","XHB",
    "KRE","KBE","JETS","XOP","OIH","XME","ARKK","ARKG","BOTZ","ROBO","LIT","TAN","ICLN",
    # 债券
    "AGG","BND","TLT","IEF","SHY","LQD","HYG","JNK","MUB","TIP","BNDX","VCIT","VCSH",
    "EMB","BIL","SGOV",
    # 国际/新兴
    "EFA","VEA","IEFA","VWO","IEMG","EEM","VXUS","INDA","FXI","MCHI","EWJ","EWZ","EWY","EWT",
    # 大宗/黄金
    "GLD","IAU","SLV","GDX","GDXJ","DBC","USO","PDBC",
    # 收益增强
    "JEPI","JEPQ","QYLD",
]


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS us_etfs (
        ticker TEXT PRIMARY KEY, name TEXT, category TEXT, fund_family TEXT,
        aum REAL, expense_ratio REAL, is_active INTEGER DEFAULT 1,
        updated_at TEXT DEFAULT (datetime('now')))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS us_etf_profile (
        ticker TEXT PRIMARY KEY, data TEXT, updated_at TEXT)""")
    # 复权列迁移(与个股共享 us_daily_quotes)
    have = {r[1] for r in conn.execute("PRAGMA table_info(us_daily_quotes)")}
    if "adj_close" not in have:  # 表还没建则先建
        conn.execute("""CREATE TABLE IF NOT EXISTS us_daily_quotes (
            ticker TEXT, trade_date TEXT, open REAL, high REAL, low REAL, close REAL,
            adj_close REAL, volume REAL, adj_open REAL, adj_high REAL, adj_low REAL,
            PRIMARY KEY (ticker, trade_date))""")
    conn.commit()


def sync_profiles(conn: sqlite3.Connection, tickers: list[str]) -> int:
    """profile(持仓/费率/类别/板块)—— 变动慢,周频刷新即可。"""
    import datetime as dt
    print(f"[ETF] profile: 精选 {len(tickers)} 只 ...")
    ok = 0
    for i, tk in enumerate(tickers, 1):
        p = fetch_us_etf_profile(tk)
        if not p:
            continue
        conn.execute(
            """INSERT INTO us_etfs (ticker,name,category,fund_family,aum,expense_ratio,is_active,updated_at)
               VALUES (?,?,?,?,?,?,1,datetime('now'))
               ON CONFLICT(ticker) DO UPDATE SET name=excluded.name,category=excluded.category,
                 fund_family=excluded.fund_family,aum=excluded.aum,expense_ratio=excluded.expense_ratio,
                 is_active=1,updated_at=datetime('now')""",
            (tk, p.get("name"), p.get("category"), p.get("fund_family"),
             p.get("aum"), p.get("expense_ratio")))
        conn.execute("INSERT OR REPLACE INTO us_etf_profile(ticker,data,updated_at) VALUES(?,?,?)",
                     (tk, json.dumps(p, ensure_ascii=False), dt.datetime.now().isoformat()))
        ok += 1
        if i % 25 == 0:
            conn.commit()
        time.sleep(0.3)
    conn.commit()
    print(f"[ETF] profile 完成 {ok}/{len(tickers)}")
    return ok


def sync_quotes(conn: sqlite3.Connection, tickers: list[str], period: str = "5d") -> int:
    """行情 —— 日频(默认近5日增量刷新;首建可传 2y)。写共享 us_daily_quotes。"""
    print(f"[ETF] 行情({period}, 批量)...")
    total = 0
    for i in range(0, len(tickers), 60):
        batch = tickers[i:i + 60]
        rows = fetch_us_quotes_bulk(batch, period=period)
        if not rows:
            time.sleep(6); rows = fetch_us_quotes_bulk(batch, period=period)
        if rows:
            conn.executemany(
                """INSERT OR REPLACE INTO us_daily_quotes
                   (ticker,trade_date,open,high,low,close,adj_open,adj_high,adj_low,adj_close,volume)
                   VALUES (:ticker,:trade_date,:open,:high,:low,:close,
                    :adj_open,:adj_high,:adj_low,:adj_close,:volume)""", rows)
            conn.commit(); total += len(rows)
        time.sleep(1.5)
    print(f"[ETF] 行情写入 {total} 行")
    return total


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--quotes-only", action="store_true", help="只刷行情(日频)")
    ap.add_argument("--profiles-only", action="store_true", help="只刷 profile(周频)")
    ap.add_argument("--quotes-period", default="5d", help="行情周期(首建可传 2y/max)")
    args = ap.parse_args()

    conn = sqlite3.connect(config.US_DB_PATH, timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure(conn)
    tickers = sorted(set(CURATED))
    do_prof = not args.quotes_only
    do_quotes = not args.profiles_only
    if do_prof:
        sync_profiles(conn, tickers)
    if do_quotes:
        sync_quotes(conn, tickers, period=args.quotes_period)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.OperationalError:
        pass
    conn.close()


if __name__ == "__main__":
    main()
