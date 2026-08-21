"""美股列表 + 行情 API。读独立库 us.db(只读连接),不参与 A股/ETF 打分。"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Query

import config

router = APIRouter(prefix="/api/us", tags=["us"])

_SORT = {"dollar_vol": "q.close*q.volume", "pct_chg": "chg", "close": "q.close", "volume": "q.volume"}


def _ro_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{config.US_DB_PATH}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _rw_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.US_DB_PATH, timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    conn.row_factory = sqlite3.Row
    return conn


def _migrate_adj(conn: sqlite3.Connection) -> None:
    """老库补复权 OHLC 列(幂等)。"""
    have = {r[1] for r in conn.execute("PRAGMA table_info(us_daily_quotes)")}
    for col in ("adj_open", "adj_high", "adj_low"):
        if col not in have:
            conn.execute(f"ALTER TABLE us_daily_quotes ADD COLUMN {col} REAL")
    conn.commit()


# 同一 ticker 并发首刷去重(避免多次点开同时拉 yfinance max)
_ensuring: set[str] = set()
_ensure_lock = __import__("threading").Lock()


def _ensure_history(ticker: str, min_years: float = 3.0) -> None:
    """懒加载深历史:库内历史不足(浅于 min_years 或行数太少)时,一次性拉 yfinance max 存库。

    首次点开某只稍慢(~1-3s),之后秒开;周/月/年K 需要多年日线,故按需拉深。
    """
    import datetime as dt

    try:
        conn = _ro_conn()
    except sqlite3.OperationalError:
        return
    try:
        row = conn.execute(
            "SELECT COUNT(*), MIN(trade_date), COUNT(adj_open) FROM us_daily_quotes WHERE ticker=?",
            (ticker,),
        ).fetchone()
        cnt = row[0] if row else 0
        earliest = row[1] if row else None
        adj_cnt = row[2] if row else 0
    except sqlite3.OperationalError:  # 老库尚无 adj_open 列
        cnt, earliest, adj_cnt = 0, None, 0
    finally:
        conn.close()

    deep_enough = False
    if earliest and cnt >= 400 and adj_cnt >= cnt:  # 需已复权填充,否则重拉一次补 adj
        try:
            years = (dt.date.today() - dt.date.fromisoformat(earliest)).days / 365.25
            deep_enough = years >= min_years
        except ValueError:
            deep_enough = False
    if deep_enough:
        return

    with _ensure_lock:
        if ticker in _ensuring:
            return
        _ensuring.add(ticker)
    try:
        from services.us_market_adapter import fetch_us_quote_tiingo, fetch_us_quotes_bulk
        # 深历史优先 Tiingo(可靠、不限流、免费档给全历史),yfinance max 兜底
        bars: list[dict] = []
        try:
            bars = [b for b in fetch_us_quote_tiingo(ticker, "2010-01-01") if b.get("close") is not None]
        except Exception:  # noqa: BLE001
            bars = []
        if not bars:
            bars = fetch_us_quotes_bulk([ticker], period="max")
        if not bars:
            return
        conn = _rw_conn()
        try:
            _migrate_adj(conn)
            conn.executemany(
                """INSERT OR REPLACE INTO us_daily_quotes
                   (ticker, trade_date, open, high, low, close,
                    adj_open, adj_high, adj_low, adj_close, volume)
                   VALUES (:ticker,:trade_date,:open,:high,:low,:close,
                    :adj_open,:adj_high,:adj_low,:adj_close,:volume)""",
                bars,
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001  首刷失败不致命,退回浅历史
        pass
    finally:
        with _ensure_lock:
            _ensuring.discard(ticker)


def _aggregate(daily: list[dict], freq: str) -> list[dict]:
    """日线 → 周/月/年 K:同组 开=首、高=max、低=min、收=尾、量=sum;time 取组内末交易日。"""
    if freq == "D" or not daily:
        return daily

    def key(d: str) -> str:
        y, m, dd = d[:4], d[5:7], d[8:10]
        if freq == "Y":
            return y
        if freq == "M":
            return f"{y}-{m}"
        # W: ISO 年-周
        import datetime as dt
        iso = dt.date(int(y), int(m), int(dd)).isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"

    out: list[dict] = []
    cur_key = None
    grp: list[dict] = []

    def flush():
        if not grp:
            return
        vols = [b["volume"] for b in grp if b.get("volume") is not None]
        out.append({
            "trade_date": grp[-1]["trade_date"],
            "open": grp[0]["open"],
            "high": max(b["high"] for b in grp if b["high"] is not None),
            "low": min(b["low"] for b in grp if b["low"] is not None),
            "close": grp[-1]["close"],
            "volume": sum(vols) if vols else None,
        })

    for b in daily:
        k = key(b["trade_date"])
        if k != cur_key:
            flush()
            grp = []
            cur_key = k
        grp.append(b)
    flush()
    return out


@router.get("/list")
async def us_list(
    q: str | None = Query(None, description="按 ticker/名称模糊搜索"),
    sort: str = Query("dollar_vol", description="dollar_vol|pct_chg|close|volume"),
    limit: int = Query(200, ge=1, le=3000),
):
    """美股列表(最新交易日行情 + 日涨跌幅)。默认按成交额(close×volume)排序。"""
    try:
        conn = _ro_conn()
    except sqlite3.OperationalError:
        return {"as_of": None, "count": 0, "stocks": [], "error": "美股库未就绪(先跑 us_sync)"}
    try:
        latest_row = conn.execute("SELECT MAX(trade_date) FROM us_daily_quotes").fetchone()
        latest = latest_row[0] if latest_row else None
        sort_col = _SORT.get(sort, "q.close*q.volume")

        where = ["s.is_active=1", "q.trade_date=?"]
        params: list = [latest]
        if q:
            where.append("(s.ticker LIKE ? OR s.name LIKE ?)")
            params += [f"%{q.upper()}%", f"%{q}%"]
        params.append(limit)

        rows = conn.execute(
            f"""SELECT s.ticker, s.name, s.exchange, q.close, q.volume, q.trade_date,
                       (SELECT close FROM us_daily_quotes p
                        WHERE p.ticker=s.ticker AND p.trade_date<q.trade_date
                        ORDER BY p.trade_date DESC LIMIT 1) AS prev_close,
                       CASE WHEN (SELECT close FROM us_daily_quotes p
                                  WHERE p.ticker=s.ticker AND p.trade_date<q.trade_date
                                  ORDER BY p.trade_date DESC LIMIT 1) > 0
                            THEN round((q.close / (SELECT close FROM us_daily_quotes p
                                  WHERE p.ticker=s.ticker AND p.trade_date<q.trade_date
                                  ORDER BY p.trade_date DESC LIMIT 1) - 1) * 100, 2)
                            ELSE NULL END AS chg
                FROM us_stocks s
                JOIN us_daily_quotes q ON q.ticker=s.ticker AND q.trade_date=?
                WHERE {' AND '.join(where)}
                ORDER BY {sort_col} IS NULL, {sort_col} DESC
                LIMIT ?""",
            [latest] + params,
        ).fetchall()
        return {"as_of": latest, "count": len(rows), "stocks": [dict(r) for r in rows]}
    finally:
        conn.close()


@router.get("/{ticker}/klines")
def us_klines(
    ticker: str,
    freq: str = Query("D", description="D日|W周|M月|Y年"),
    adjust: bool = Query(True, description="是否前复权(默认是;拆股/分红回溯调整)"),
):
    """K线数据(OHLCV)。首次访问懒加载深历史(Tiingo优先);周/月/年由日线聚合。

    adjust=True 用复权 OHLC(adj_*),消除拆股断崖;缺复权值时按行回退原始价。
    sync def:FastAPI 用线程池跑,yfinance/聚合的阻塞不冻结事件循环。
    """
    ticker = ticker.upper()
    freq = freq.upper() if freq.upper() in {"D", "W", "M", "Y"} else "D"
    _ensure_history(ticker)
    try:
        conn = _ro_conn()
    except sqlite3.OperationalError:
        return {"ticker": ticker, "freq": freq, "bars": [], "error": "美股库未就绪"}
    try:
        rows = conn.execute(
            "SELECT trade_date, open, high, low, close, volume, "
            "adj_open, adj_high, adj_low, adj_close FROM us_daily_quotes "
            "WHERE ticker=? ORDER BY trade_date", (ticker,)
        ).fetchall()
    finally:
        conn.close()

    daily = []
    for r in rows:
        m = dict(r)
        if adjust:  # 复权:有 adj_* 用之,缺则回退该行原始价(逐字段回退,避免半复权错位)
            o = m["adj_open"] if m["adj_open"] is not None else m["open"]
            h = m["adj_high"] if m["adj_high"] is not None else m["high"]
            lo = m["adj_low"] if m["adj_low"] is not None else m["low"]
            c = m["adj_close"] if m["adj_close"] is not None else m["close"]
        else:
            o, h, lo, c = m["open"], m["high"], m["low"], m["close"]
        daily.append({"trade_date": m["trade_date"], "open": o, "high": h,
                      "low": lo, "close": c, "volume": m["volume"]})
    bars = _aggregate(daily, freq)
    return {"ticker": ticker, "freq": freq, "adjust": adjust, "count": len(bars), "bars": bars}


@router.get("/{ticker}")
async def us_detail(ticker: str):
    """单只美股详情:本地行情(近120日)+ Tiingo 实时基本面(市值/PE/PB/PEG)+ 简介。"""
    ticker = ticker.upper()
    try:
        conn = _ro_conn()
    except sqlite3.OperationalError:
        return {"error": "美股库未就绪"}
    try:
        meta = conn.execute(
            "SELECT ticker, name, exchange FROM us_stocks WHERE ticker=?", (ticker,)
        ).fetchone()
        if not meta:
            return {"error": "未找到该美股"}
        q = conn.execute(
            "SELECT trade_date, close, volume FROM us_daily_quotes WHERE ticker=? "
            "ORDER BY trade_date DESC LIMIT 120", (ticker,)
        ).fetchall()
        quotes = [dict(r) for r in reversed(q)]
    finally:
        conn.close()

    result: dict = {"ticker": ticker, "name": meta["name"], "exchange": meta["exchange"],
                    "quotes": quotes}
    # 基本面(Tiingo 实时,单只按需)
    try:
        from services.us_market_adapter import fetch_us_fundamentals_daily, fetch_us_meta
        result["fundamentals"] = fetch_us_fundamentals_daily(ticker) or {}
        m = fetch_us_meta(ticker)
        if m:
            result["description"] = m.get("description")
            if not result["name"]:
                result["name"] = m.get("name")
    except Exception as ex:  # noqa: BLE001
        result["fundamentals"] = {"error": str(ex)[:80]}
    # 富字段(yfinance:行业/分析师目标价/52周/质量成长,单只按需)
    try:
        from services.us_market_adapter import fetch_us_profile
        result["profile"] = fetch_us_profile(ticker) or {}
    except Exception as ex:  # noqa: BLE001
        result["profile"] = {"error": str(ex)[:80]}
    return result
