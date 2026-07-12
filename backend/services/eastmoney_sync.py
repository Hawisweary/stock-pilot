"""东财/ADATA 特色数据同步 — akshare 降为 fallback"""
import sqlite3
import socket
from datetime import date

socket.setdefaulttimeout(8)
from config import DB_PATH


def sync_eastmoney_data() -> dict:
    """同步融资融券、北向资金等特色数据"""
    today = date.today().strftime("%Y-%m-%d")
    results = {"margin": 0, "north": 0, "sources": {}}

    # 1. 融资融券 — 东财 datacenter 全跟踪池历史
    margin_src = "eastmoney"
    try:
        from services.margin_balance_sync import sync_margin_balance

        mr = sync_margin_balance()
        results["margin"] = mr.get("rows_written", 0)
    except Exception as e:
        print(f"[东财] datacenter 融资融券失败: {e}")
        margin_src = "akshare"
        try:
            from services.akshare_lazy import akshare as _ak

            ak = _ak()
            df_sse = ak.stock_margin_detail_sse(date=today)
            margin_records = []
            if df_sse is not None and not df_sse.empty:
                for _, r in df_sse.iterrows():
                    code = str(r.get("股票代码", ""))
                    if len(code) == 6 and code.startswith("6"):
                        margin_records.append((code, r.get("融资余额", 0), r.get("融券余量", 0)))
            if margin_records:
                conn = sqlite3.connect(DB_PATH)
                from services.margin_balance_sync import _ensure_table

                _ensure_table(conn)
                for code, mb, sb in margin_records:
                    row = conn.execute(
                        "SELECT id FROM stocks WHERE code=?", (code,)
                    ).fetchone()
                    if not row:
                        continue
                    conn.execute(
                        """INSERT OR REPLACE INTO eastmoney_margin
                        (stock_id, date, margin_balance, margin_buy)
                        VALUES (?,?,?,?)""",
                        (
                            int(row[0]),
                            today,
                            float(mb) if mb else 0,
                            float(sb) if sb else 0,
                        ),
                    )
                conn.commit()
                conn.close()
                results["margin"] = len(margin_records)
        except Exception as e2:
            print(f"[东财] akshare 融资融券 fallback 失败: {e2}")

    results["sources"]["margin"] = margin_src

    # 2. 北向 — ADATA 优先，akshare fallback
    north_src = "adata"
    try:
        from services.adata_adapter import get_north_flow

        rows = get_north_flow(days=10)
        if rows:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("""CREATE TABLE IF NOT EXISTS north_flow_daily (
                date TEXT PRIMARY KEY, net_flow REAL, buy_amount REAL, sell_amount REAL)""")
            for r in rows:
                conn.execute(
                    "INSERT OR REPLACE INTO north_flow_daily (date, net_flow, buy_amount, sell_amount) VALUES (?,?,?,?)",
                    (r["date"], r.get("net_flow"), r.get("buy_amount"), r.get("sell_amount")),
                )
            conn.commit()
            conn.close()
            results["north"] = len(rows)
    except Exception as e:
        print(f"[东财] ADATA 北向失败: {e}")
        north_src = "akshare"
        try:
            from services.akshare_lazy import akshare as _ak

            ak = _ak()
            try:
                df_hsgt = ak.stock_hsgt_individual_em(stock="", market="", date_type="1")
            except Exception:
                df_hsgt = ak.stock_hsgt_individual_em()
            if df_hsgt is not None and not df_hsgt.empty:
                conn = sqlite3.connect(DB_PATH)
                conn.execute("""CREATE TABLE IF NOT EXISTS eastmoney_holdings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, date TEXT,
                    shares REAL, ratio REAL, UNIQUE(code, date))""")
                count = 0
                for _, r in df_hsgt.iterrows():
                    code = str(r.iloc[0])[:6] if len(str(r.iloc[0])) >= 6 else str(r.iloc[0])
                    shares = float(r.iloc[1]) if len(r) > 1 else 0
                    ratio = float(r.iloc[2]) if len(r) > 2 else 0
                    conn.execute(
                        "INSERT OR REPLACE INTO eastmoney_holdings (code,date,shares,ratio) VALUES (?,?,?,?)",
                        (code, today, shares, ratio),
                    )
                    count += 1
                conn.commit()
                conn.close()
                results["north"] = count
        except Exception as e2:
            print(f"[东财] akshare 北向 fallback 失败: {e2}")

    results["sources"]["north"] = north_src
    return {"date": today, "results": results}
