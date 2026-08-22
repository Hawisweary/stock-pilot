"""美股 ETF 列表 + 详情 API。读 us.db(us_etfs / us_etf_profile),行情复用 us_daily_quotes。

K线复用 /api/us/{ticker}/klines(同表),本模块只提供列表与 ETF 富字段详情。
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3

from fastapi import APIRouter, Query

import config

router = APIRouter(prefix="/api/us-etf", tags=["us-etf"])

_SORT = {"aum": "e.aum", "dollar_vol": "q.close*q.volume", "pct_chg": "chg",
         "close": "q.close", "expense": "e.expense_ratio"}


def _ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{config.US_DB_PATH}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


@router.get("/list")
async def etf_list(
    q: str | None = Query(None),
    sort: str = Query("aum", description="aum|dollar_vol|pct_chg|close|expense"),
    limit: int = Query(300, ge=1, le=1000),
):
    """ETF 列表(元信息 + 最新行情 + 日涨跌)。默认按规模(AUM)排序。"""
    try:
        conn = _ro()
    except sqlite3.OperationalError:
        return {"as_of": None, "count": 0, "etfs": [], "error": "ETF 库未就绪(先跑 us_etf_sync)"}
    try:
        # 最新交易日按「ETF 池」算(共享表里加密/外汇周末交易会顶高全表 MAX)
        latest = conn.execute(
            "SELECT MAX(q.trade_date) FROM us_daily_quotes q "
            "JOIN us_etfs e ON e.ticker=q.ticker WHERE e.is_active=1"
        ).fetchone()[0]
        sort_col = _SORT.get(sort, "e.aum")
        where = ["e.is_active=1"]
        params: list = [latest]
        if q:
            where.append("(e.ticker LIKE ? OR e.name LIKE ? OR e.category LIKE ?)")
            params += [f"%{q.upper()}%", f"%{q}%", f"%{q}%"]
        params.append(limit)
        rows = conn.execute(
            f"""SELECT e.ticker,e.name,e.category,e.fund_family,e.aum,e.expense_ratio,
                       q.close,q.volume,q.trade_date,
                       (SELECT close FROM us_daily_quotes p WHERE p.ticker=e.ticker
                        AND p.trade_date<q.trade_date ORDER BY p.trade_date DESC LIMIT 1) AS prev,
                       CASE WHEN (SELECT close FROM us_daily_quotes p WHERE p.ticker=e.ticker
                            AND p.trade_date<q.trade_date ORDER BY p.trade_date DESC LIMIT 1)>0
                        THEN round((q.close/(SELECT close FROM us_daily_quotes p WHERE p.ticker=e.ticker
                            AND p.trade_date<q.trade_date ORDER BY p.trade_date DESC LIMIT 1)-1)*100,2)
                        ELSE NULL END AS chg
                FROM us_etfs e
                LEFT JOIN us_daily_quotes q ON q.ticker=e.ticker AND q.trade_date=?
                WHERE {' AND '.join(where)}
                ORDER BY {sort_col} IS NULL, {sort_col} DESC
                LIMIT ?""",
            params,
        ).fetchall()
        return {"as_of": latest, "count": len(rows), "etfs": [dict(r) for r in rows]}
    finally:
        conn.close()


_ETF_INTRO_SYS = "你是专业的 ETF 投资顾问。基于给定数据用简洁中文写投研简介。只输出 JSON,不要多余文字。"


def _gen_etf_cn_intro(ticker: str, p: dict) -> dict | None:
    """ETF 中文简介(DeepSeek,懒加载单只按需)。数据不足或 LLM 不可用则 None。"""
    if not (p.get("category") or p.get("overview") or p.get("holdings")):
        return None
    try:
        from services.llm_client import chat_completion, is_llm_available
        if not is_llm_available():
            return None
    except Exception:
        return None
    aum = p.get("aum")
    alloc = " ".join(f"{k}:{round(v*100)}%" for k, v in (p.get("asset_classes") or {}).items() if v and v > 0.005) or "—"
    hs = p.get("holdings") or []
    hold = " ".join(f"{h['symbol']}({round((h.get('weight') or 0)*100,1)}%)" for h in hs[:10]) or "无(债券/大宗类)"
    secs = sorted(p.get("sectors") or [], key=lambda s: -(s.get("weight") or 0))[:5]
    sec = " ".join(f"{s['sector']}:{round((s.get('weight') or 0)*100)}%" for s in secs) or "—"
    prompt = (
        f"为这只美股 ETF 写中文简介。数据:\n代码:{ticker} 名称:{p.get('name') or ticker}\n"
        f"类别:{p.get('category') or '—'} 发行商:{p.get('fund_family') or '—'} 规模:{f'${round(aum/1e9,1)}B' if aum else '—'}\n"
        f"资产配置:{alloc}\n前十持仓:{hold}\n板块权重:{sec}\n英文说明(参考):{(p.get('overview') or '')[:400]}\n\n"
        "输出 JSON,五字段各一句话(每句≤45字,客观、不构成投资建议):\n"
        '{"what":"这只ETF是什么","tracks":"跟踪的指数或策略","holdings":"持仓特征","fit":"适合什么投资者","risk":"主要风险"}'
    )
    try:
        intro = json.loads(chat_completion(prompt, system_prompt=_ETF_INTRO_SYS, max_tokens=500,
                                           temperature=0.4, json_mode=True, max_retries=1))
        if isinstance(intro, dict) and intro.get("what"):
            return {k: intro.get(k) for k in ("what", "tracks", "holdings", "fit", "risk")}
    except Exception:  # noqa: BLE001
        return None
    return None


def _get_etf_profile(ticker: str, ttl_hours: float = 20.0) -> dict:
    """ETF profile 缓存优先;冷门/过期则现抓并回写。"""
    try:
        conn = sqlite3.connect(config.US_DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError:
        from services.us_market_adapter import fetch_us_etf_profile
        return fetch_us_etf_profile(ticker) or {}
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS us_etf_profile (
            ticker TEXT PRIMARY KEY, data TEXT, updated_at TEXT)""")
        row = conn.execute("SELECT data,updated_at FROM us_etf_profile WHERE ticker=?", (ticker,)).fetchone()
        if row:
            try:
                age = (dt.datetime.now() - dt.datetime.fromisoformat(row["updated_at"])).total_seconds()
                if age < ttl_hours * 3600:
                    return json.loads(row["data"])
            except (ValueError, TypeError):
                pass
        from services.us_market_adapter import fetch_us_etf_profile
        p = fetch_us_etf_profile(ticker)
        if not p:
            return json.loads(row["data"]) if row else {}
        if row:  # 保留已生成的中文简介
            try:
                prev = json.loads(row["data"])
                if prev.get("cn_intro"):
                    p["cn_intro"] = prev["cn_intro"]
            except (TypeError, ValueError):
                pass
        if not p.get("cn_intro"):  # 缺失则懒生成一次(打开时)
            p["cn_intro"] = _gen_etf_cn_intro(ticker, p)
        conn.execute("INSERT OR REPLACE INTO us_etf_profile(ticker,data,updated_at) VALUES(?,?,?)",
                     (ticker, json.dumps(p, ensure_ascii=False), dt.datetime.now().isoformat()))
        conn.commit()
        return p
    finally:
        conn.close()


@router.get("/{ticker}")
def etf_detail(ticker: str):
    """单只 ETF 详情:元信息 + profile(持仓/板块/配置/费率/回报)+ 近120日行情。K线另走 /api/us/{ticker}/klines。"""
    ticker = ticker.upper()
    try:
        conn = _ro()
    except sqlite3.OperationalError:
        return {"error": "ETF 库未就绪"}
    try:
        meta = conn.execute("SELECT ticker,name,category,fund_family,aum,expense_ratio FROM us_etfs WHERE ticker=?",
                            (ticker,)).fetchone()
        qq = conn.execute("SELECT trade_date,close,volume FROM us_daily_quotes WHERE ticker=? "
                          "ORDER BY trade_date DESC LIMIT 120", (ticker,)).fetchall()
        quotes = [dict(r) for r in reversed(qq)]
    finally:
        conn.close()
    if not meta:
        return {"error": "未找到该 ETF"}
    prof = _get_etf_profile(ticker)
    return {"ticker": ticker, "name": meta["name"] or prof.get("name"),
            "category": meta["category"], "fund_family": meta["fund_family"],
            "quotes": quotes, "profile": prof}
