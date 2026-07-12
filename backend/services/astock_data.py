"""a-stock-data V3.1 集成 — akshare 直连替代 datacenter-web API"""
import sqlite3, requests, json, socket
from datetime import date, timedelta

socket.setdefaulttimeout(10)
from config import DB_PATH
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


# ═══════════════════ 1. 同花顺热点 ═══════════════════

def _ths_change_pct(row: dict) -> float:
    """同花顺 getharden 接口涨幅字段为 zhangfu（百分数）。"""
    pct = row.get("zhangfu")
    if pct is None or pct == "":
        pct = row.get("changepercent")
    try:
        return round(float(pct or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _fetch_ths_hotspot_rows(trade_date: str | None = None) -> list[dict]:
    today = (trade_date or date.today().strftime("%Y-%m-%d"))[:10]
    r = requests.get(
        f"http://zx.10jqka.com.cn/event/api/getharden/date/{today}/orderby/date/orderway/desc/charset/GBK/",
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"},
        timeout=10,
    )
    data = r.json()
    if data.get("errocode", 0) != 0:
        raise RuntimeError(data.get("errormsg") or "同花顺热点接口错误")
    return data.get("data") or []


def sync_ths_hotspots(trade_date: str | None = None) -> dict:
    """同花顺当日强势股归因 — 写入 ths_hotspots"""
    today = (trade_date or date.today().strftime("%Y-%m-%d"))[:10]
    try:
        rows = _fetch_ths_hotspot_rows(today)
    except Exception as e:
        return {"error": f"同花顺热点失败: {e}"}
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS ths_hotspots (
        date TEXT, code TEXT, name TEXT, reason TEXT, change_pct REAL, PRIMARY KEY (date, code))""")
    nonzero = 0
    for row in rows:
        code = str(row.get("code", "")).zfill(6)
        if len(code) != 6:
            continue
        change_pct = _ths_change_pct(row)
        if change_pct != 0:
            nonzero += 1
        conn.execute(
            "INSERT OR REPLACE INTO ths_hotspots (date,code,name,reason,change_pct) VALUES (?,?,?,?,?)",
            (today, code, row.get("name", ""), row.get("reason", ""), change_pct),
        )
    conn.commit()
    conn.close()
    return {"date": today, "count": len(rows), "nonzero_pct": nonzero}


# ═══════════════════ 2. 概念板块归属 — 三层拼合 ═══════════════════
# 层1: stocks.industry_sw（申万行业，本地，100%覆盖）
# 层2: industry_tags（用户自定义概念，本地）
# 层3: stock_concept_boards 缓存（东财 slist 批量写入后生效）

def _fetch_em_boards(code: str) -> list[dict]:
    """从东财 slist 获取个股概念板块，直连绕代理。slist 有时被封返回空，正常现象。"""
    from services.http_client import get as http_get
    market = "1" if code.startswith(("6", "9")) else "0"
    url = (
        "https://push2.eastmoney.com/api/qt/slist/get"
        f"?sListType=3&secid={market}.{code}&fields=f12,f14,f62"
    )
    try:
        res = http_get(url).json()
    except Exception:
        return []
    items = (res or {}).get("data", {})
    if isinstance(items, dict):
        items = items.get("diff", []) or []
    boards = []
    for item in (items or []):
        bk_code = item.get("f12", "")
        name = item.get("f14", "")
        chg_pct = item.get("f62")
        if not name:
            continue
        btype = "行业" if bk_code.startswith("BK0") else "地域" if bk_code.startswith("BK09") else "概念"
        boards.append({
            "bk_code": bk_code,
            "name": name,
            "type": btype,
            "chg_pct": round(float(chg_pct), 2) if chg_pct else None,
        })
    return boards


def concept_boards(code: str) -> list[dict]:
    """三层拼合板块标签：申万行业（本地）+ 用户概念标签（本地）+ 东财缓存（批量同步后）。"""
    boards: list[dict] = []
    seen_names: set[str] = set()

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        # 层1：申万行业（stocks.industry_sw，100%覆盖）
        stock = conn.execute(
            "SELECT id, industry_sw FROM stocks WHERE code=?", (code,)
        ).fetchone()
        stock_id = stock["id"] if stock else None
        if stock and stock["industry_sw"]:
            name = stock["industry_sw"]
            boards.append({"bk_code": "", "name": name, "type": "行业", "chg_pct": None})
            seen_names.add(name)

        # 层2：用户自定义概念标签
        if stock_id:
            for r in conn.execute(
                """SELECT it.name FROM industry_tags it
                   JOIN stock_industries si ON si.industry_id=it.id
                   WHERE si.stock_id=?""",
                (stock_id,),
            ).fetchall():
                if r["name"] not in seen_names:
                    boards.append({"bk_code": "", "name": r["name"], "type": "概念", "chg_pct": None})
                    seen_names.add(r["name"])

        # 层3：东财 slist 批量缓存（sync_concept_boards_all 写入后生效）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stock_concept_boards (
                stock_id INTEGER NOT NULL, bk_code TEXT NOT NULL,
                name TEXT NOT NULL, type TEXT NOT NULL DEFAULT '概念',
                fetched_date TEXT NOT NULL, PRIMARY KEY (stock_id, bk_code)
            )
        """)
        if stock_id:
            for r in conn.execute(
                "SELECT bk_code, name, type FROM stock_concept_boards WHERE stock_id=?",
                (stock_id,),
            ).fetchall():
                if r["name"] not in seen_names:
                    boards.append({"bk_code": r["bk_code"], "name": r["name"],
                                   "type": r["type"], "chg_pct": None})
                    seen_names.add(r["name"])
        conn.close()
    except Exception:
        stock_id = None

    # 层3 缓存空时，实时拉取东财（可能因 IP 封锁返回空）
    if stock_id and not any(b["bk_code"] for b in boards):
        em_boards = _fetch_em_boards(code)
        if em_boards:
            _save_em_boards(stock_id, em_boards)
            for b in em_boards:
                if b["name"] not in seen_names:
                    boards.append(b)
                    seen_names.add(b["name"])

    return boards


def _save_em_boards(stock_id: int, boards: list[dict]) -> None:
    today = date.today().isoformat()
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.executemany(
            """INSERT OR REPLACE INTO stock_concept_boards
               (stock_id, bk_code, name, type, fetched_date) VALUES (?,?,?,?,?)""",
            [(stock_id, b["bk_code"], b["name"], b["type"], today) for b in boards],
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def sync_concept_boards_all() -> dict:
    """批量同步全部持仓股票的东财板块数据到本地缓存，限流间隔 1s。"""
    import time
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_concept_boards (
            stock_id  INTEGER NOT NULL,
            bk_code   TEXT NOT NULL,
            name      TEXT NOT NULL,
            type      TEXT NOT NULL DEFAULT '概念',
            fetched_date TEXT NOT NULL,
            PRIMARY KEY (stock_id, bk_code)
        )
    """)
    stocks = conn.execute("SELECT id, code FROM stocks WHERE is_active=1").fetchall()
    conn.close()

    today = date.today().isoformat()
    ok, failed = 0, 0
    for s in stocks:
        try:
            boards = _fetch_em_boards(s["code"])
            if boards:
                c = sqlite3.connect(DB_PATH)
                c.executemany(
                    """INSERT OR REPLACE INTO stock_concept_boards
                       (stock_id, bk_code, name, type, fetched_date) VALUES (?,?,?,?,?)""",
                    [(s["id"], b["bk_code"], b["name"], b["type"], today) for b in boards],
                )
                c.commit()
                c.close()
                ok += 1
            time.sleep(1)
        except Exception:
            failed += 1
    return {"synced": ok, "failed": failed, "total": len(stocks), "date": today}


# ═══════════════════ 2b. 龙虎榜 — ADATA/东财 + akshare 兜底 ═══════════════════

def dragon_tiger_board(code: str, report_date: str | None = None) -> dict:
    from services.lhb_fetch import dragon_tiger_board as _lhb

    return _lhb(code, report_date)


# ═══════════════════ 3. 解禁 — akshare ═══════════════════

def unlock_calendar(code: str, days: int = 90) -> list:
    try:
        from services.akshare_lazy import akshare as _ak

        df = _ak().stock_restricted_release_summary_em(symbol=code)
        if df is None or df.empty: return []
        result = []
        for _, r in df.iterrows():
            result.append({"date": str(r.iloc[0])[:15], "shares": round(float(r.iloc[1] or 0) / 10000, 1),
                           "ratio": float(r.iloc[2] or 0) if len(r) > 2 else 0})
        return result[:10]
    except: return []


# ═══════════════════ 4. 融资融券 — akshare ═══════════════════

def margin_trading(code: str) -> list:
    try:
        from services.data_sources import margin_trading as em_margin

        rows = em_margin(code, page_size=5)
        if rows:
            latest = rows[0]
            return [{
                "date": latest.get("date", date.today().strftime("%Y-%m-%d")),
                "margin_balance": round(float(latest.get("rzye") or 0), 2),
                "margin_buy": round(float(latest.get("rzmre") or 0), 2),
            }]
    except Exception as e:
        print(f"margin eastmoney {code}: {e}")
    try:
        from services.akshare_lazy import akshare as _ak

        today = date.today().strftime("%Y-%m-%d")
        if code.startswith("6"):
            df = _ak().stock_margin_detail_sse(date=today)
        else:
            df = _ak().stock_margin_detail_szse(date=today)
        if df is not None and not df.empty:
            for _, r in df.iterrows():
                if str(r.iloc[0]).startswith(code):
                    return [{"date": today, "margin_balance": round(float(r.iloc[1] or 0), 2),
                             "margin_buy": round(float(r.iloc[2] or 0), 2)}]
    except Exception as e:
        print(f"margin akshare {code}: {e}")
    return []


# ═══════════════════ 5. 大宗交易 — akshare ═══════════════════

def block_trade(code: str) -> list:
    try:
        from services.akshare_lazy import akshare as _ak

        df = _ak().stock_dzjy_mrmx(symbol="A股", start_date=(date.today() - timedelta(days=60)).strftime("%Y-%m-%d"),
                                end_date=date.today().strftime("%Y-%m-%d"))
        if df is None or df.empty: return []
        mask = df.iloc[:, 1].astype(str).str.contains(code)
        df = df[mask]
        return [{"date": str(r.iloc[0])[:10], "price": float(r.iloc[3] or 0),
                 "amount": float(r.iloc[4] or 0), "buyer": str(r.iloc[5])[:20],
                 "seller": str(r.iloc[6])[:20]} for _, r in df.iterrows()][:10]
    except: return []


# ═══════════════════ 6. 股东户数 — akshare ═══════════════════

def shareholder_change(code: str) -> list:
    try:
        from services.akshare_lazy import akshare as _ak

        df = _ak().stock_holder_change_em(symbol=code)
        if df is None or df.empty: return []
        return [{"date": str(r.iloc[0])[:10], "holders": int(r.iloc[1] or 0)}
                for _, r in df.tail(8).iterrows()]
    except: return []


# ═══════════════════ 7. 分红 — akshare ═══════════════════

def dividend_history(code: str) -> list:
    try:
        from services.data_sources import dividend_history as em_div

        rows = em_div(code, page_size=10)
        if rows:
            return [{"date": r.get("date", ""), "cash_div": float(r.get("bonus_rmb") or 0)} for r in rows]
    except Exception:
        pass
    try:
        from services.akshare_lazy import akshare as _ak

        df = _ak().stock_history_dividend_detail(symbol=code, indicator="分红")
        if df is not None and not df.empty:
            return [{"date": str(r.iloc[0])[:10], "cash_div": float(r.iloc[1] or 0)}
                    for _, r in df.tail(10).iterrows()]
    except Exception:
        pass
    return []


# ═══════════════════ 8. mootdx 财务 ═══════════════════

def sync_mootdx_financials(code: str, stock_id: int | None = None) -> dict:
    try:
        from mootdx.quotes import Quotes
        client = Quotes.factory(market="std", timeout=5)
        prefix = "sh" if code.startswith(("6","9")) else "sz"
        data = client.finance(symbol=code, market=prefix)
    except Exception as e:
        return {"code": code, "error": str(e)}
    if not data: return {"code": code, "error": "无数据"}
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS financial_statements (
        id INTEGER PRIMARY KEY AUTOINCREMENT, stock_id INTEGER, report_date TEXT,
        report_type TEXT, revenue REAL, net_profit REAL, total_assets REAL,
        total_liabilities REAL, roe REAL, eps REAL, bvps REAL,
        UNIQUE(stock_id, report_date))""")
    if stock_id is None:
        sid_row = conn.execute("SELECT id FROM stocks WHERE code=?", (code,)).fetchone()
        if not sid_row:
            conn.close()
            return {"code": code, "error": "股票未入库"}
        stock_id = sid_row[0]
    count = 0
    reports_count = 0
    for i, row in enumerate(data[:4]):
        rd = str(row.get("report_date", ""))[:10]
        if not rd:
            continue
        rtype = "季报" if i > 0 else "年报"
        conn.execute("""INSERT OR REPLACE INTO financial_statements
            (stock_id, report_date, report_type, revenue, net_profit, total_assets, total_liabilities, roe, eps, bvps)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (stock_id, rd, rtype,
             float(row.get("total_revenue", 0) or 0), float(row.get("net_profit", 0) or 0),
             float(row.get("total_assets", 0) or 0), float(row.get("total_liability", 0) or 0),
             float(row.get("roe", 0) or 0), float(row.get("eps", 0) or 0), float(row.get("bvps", 0) or 0)))
        count += 1
        db_report_type = "annual" if i == 0 else "quarterly"
        conn.execute(
            """INSERT OR REPLACE INTO financial_reports
               (stock_id, report_date, period_end_date, report_type,
                revenue, net_profit, net_profit_parent, total_assets, total_liabilities, eps)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                stock_id,
                rd,
                rd,
                db_report_type,
                float(row.get("total_revenue", 0) or 0),
                float(row.get("net_profit", 0) or 0),
                float(row.get("net_profit", 0) or 0),
                float(row.get("total_assets", 0) or 0),
                float(row.get("total_liability", 0) or 0),
                float(row.get("eps", 0) or 0),
            ),
        )
        reports_count += 1
    conn.commit(); conn.close()
    return {"code": code, "records": count, "reports_count": reports_count, "stock_id": stock_id}


# ═══════════════════ 9. 新浪财报 ═══════════════════

def sina_financial_report(code: str, report_type: str = "lrb") -> list:
    prefix = "sh" if code.startswith(("6","9")) else "sz"; ticker = f"{prefix}{code}"
    ep = {"lrb": "LRB", "fzb": "FZB", "llb": "LLB"}.get(report_type, "LRB")
    url = f"https://money.finance.sina.com.cn/corp/go.php/vFD_{ep}/{ticker}/ctrl/part/displaytype/4.phtml"
    try:
        import pandas as pd
        dfs = pd.read_html(url)
        if not dfs or len(dfs) < 2: return []
        df = dfs[-1]
        return [{"item": str(r.iloc[0]), "value": round(float(r.iloc[-1]) / 10000, 2)}
                for _, r in df.iterrows() if len(r) >= 2 and str(r.iloc[-1]).replace(".","").replace("-","").isdigit()][:15]
    except Exception as e:
        return [{"error": str(e)}]


# ═══════════════════ 10. 同花顺一致预期 EPS ═══════════════════

def consensus_eps(code: str) -> dict:
    """同花顺分析师一致预期 EPS（近3年）。
    数据源：basic.10jqka.com.cn/stock/EPS/
    """
    try:
        prefix = "sh" if code.startswith(("6", "9")) else "sz"
        url = f"https://basic.10jqka.com.cn/stock/EPS/{prefix}{code}.json"
        r = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                "Referer": f"https://basic.10jqka.com.cn/{code}/",
            },
            timeout=10,
        )
        if r.status_code != 200:
            return {}
        data = r.json()
        # 返回格式：{year: eps_forecast, ...}
        items = data.get("data", data) if isinstance(data, dict) else {}
        if not items:
            return {}
        forecasts = []
        for year, val in sorted(items.items()):
            try:
                forecasts.append({"year": str(year), "eps": round(float(val), 4)})
            except (TypeError, ValueError):
                continue
        return {"source": "同花顺", "forecasts": forecasts}
    except Exception:
        return {}
