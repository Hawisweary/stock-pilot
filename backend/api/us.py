"""美股列表 + 行情 API。读独立库 us.db(只读连接),不参与 A股/ETF 打分。"""
from __future__ import annotations

import json
import sqlite3
import threading

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


def _ensure_profile_table(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS us_profile (
        ticker TEXT PRIMARY KEY, data TEXT, updated_at TEXT)""")
    conn.commit()


_BULL_GRADES = {"buy", "strong buy", "overweight", "outperform", "accumulate", "add", "positive",
                "market outperform", "sector outperform", "conviction buy", "top pick",
                "long-term buy", "outperformer", "speculative buy", "gradually accumulate"}
_BEAR_GRADES = {"sell", "strong sell", "underweight", "underperform", "reduce", "negative",
                "market underperform", "sector underperform", "underperformer"}


def _grade_dir(grade: str) -> str | None:
    """券商评级 → 方向:bull(看涨)/bear(看空)/None(中性或无法判定)。"""
    g = (grade or "").strip().lower()
    if not g:
        return None
    if g in _BULL_GRADES:
        return "bull"
    if g in _BEAR_GRADES:
        return "bear"
    return None  # 中性(hold/neutral/equal-weight/market perform…)不计入方向验证


def _compute_accuracy(ticker: str, horizon_days: int = 365, min_samples: int = 20) -> dict | None:
    """分析师目标价准确度回测:满 horizon 的历史目标 vs 复权后真实价。

    方向准确率/命中率/MAPE/系统性偏差。目标价与真实价均复权到同基准(消除拆股)。
    """
    import bisect
    import datetime as dt

    from services.us_market_adapter import fetch_us_target_history

    _ensure_history(ticker)  # 回测需深历史,先确保
    try:
        conn = _ro_conn()
    except sqlite3.OperationalError:
        return None
    try:
        rows = conn.execute(
            "SELECT trade_date, close, adj_close FROM us_daily_quotes "
            "WHERE ticker=? AND adj_close IS NOT NULL ORDER BY trade_date", (ticker,)
        ).fetchall()
    finally:
        conn.close()
    if len(rows) < 200:
        return None
    dates = [r["trade_date"] for r in rows]
    close = {r["trade_date"]: r["close"] for r in rows}
    adj = {r["trade_date"]: r["adj_close"] for r in rows}

    targets = fetch_us_target_history(ticker)
    if not targets:
        return None

    def near(d: str):  # 最近的有价交易日(>=d)
        i = bisect.bisect_left(dates, d)
        return dates[i] if i < len(dates) else None

    today = dt.date.today()
    errs: list[float] = []
    biases: list[float] = []
    dir_hit = reach = n = 0
    # 评级方向验证:看涨评级后是否真涨、看空后是否真跌
    bull_rets: list[float] = []
    bear_rets: list[float] = []
    for t in targets:
        try:
            d = dt.date.fromisoformat(t["date"])
        except (ValueError, KeyError):
            continue
        if (today - d).days < horizon_days:
            continue
        d0 = near(d.isoformat())
        dH = near((d + dt.timedelta(days=horizon_days)).isoformat())
        if not d0 or not dH or d0 not in close or dH not in adj:
            continue
        p0, pH = adj[d0], adj[dH]
        if p0 <= 0 or pH <= 0:
            continue
        fr = pH / p0 - 1  # horizon 内复权真实收益

        # (a) 评级方向
        gd = _grade_dir(t.get("grade", ""))
        if gd == "bull":
            bull_rets.append(fr)
        elif gd == "bear":
            bear_rets.append(fr)

        # (b) 目标价(仅带目标价的记录)
        tgt = t.get("target")
        c0 = close[d0]
        if not tgt or tgt <= 0 or not c0:
            continue
        tgt_adj = tgt * (adj[d0] / c0)
        n += 1
        errs.append(abs(tgt_adj - pH) / pH)
        biases.append((tgt_adj - pH) / pH)
        if (tgt_adj > p0) == (pH > p0):
            dir_hit += 1
        i0, iH = bisect.bisect_left(dates, d0), bisect.bisect_left(dates, dH)
        seg = [adj[x] for x in dates[i0:iH + 1]]
        if seg and ((tgt_adj >= p0 and max(seg) >= tgt_adj) or (tgt_adj < p0 and min(seg) <= tgt_adj)):
            reach += 1
    if n < min_samples:
        return None

    rating = None
    if len(bull_rets) >= 10 or len(bear_rets) >= 10:
        rating = {"horizon_days": horizon_days}
        if bull_rets:
            rating["bull_n"] = len(bull_rets)
            rating["bull_win_pct"] = round(sum(1 for r in bull_rets if r > 0) / len(bull_rets) * 100)
            rating["bull_avg_pct"] = round(sum(bull_rets) / len(bull_rets) * 100, 1)
        if bear_rets:
            rating["bear_n"] = len(bear_rets)
            rating["bear_win_pct"] = round(sum(1 for r in bear_rets if r < 0) / len(bear_rets) * 100)
            rating["bear_avg_pct"] = round(sum(bear_rets) / len(bear_rets) * 100, 1)
        if bull_rets and bear_rets:  # 多空价差:看涨组比看空组多涨多少(正=评级有区分力)
            rating["spread_pct"] = round((sum(bull_rets) / len(bull_rets)
                                          - sum(bear_rets) / len(bear_rets)) * 100, 1)

    bias = sum(biases) / n
    return {
        "n": n,
        "horizon_days": horizon_days,
        "direction_pct": round(dir_hit / n * 100),
        "reach_pct": round(reach / n * 100),
        "mape_pct": round(sum(errs) / n * 100),
        "bias_pct": round(bias * 100),
        "eval_from": min(t["date"] for t in targets if (today - dt.date.fromisoformat(t["date"])).days >= horizon_days),
        "eval_to": max((d for d in dates), default=None),
        "rating": rating,
    }


def _build_profile(ticker: str) -> dict:
    """现抓整页可缓存数据:profile(行业/分析师目标价/质量)+ 分析师详情 + Tiingo基本面 + 简介。"""
    from services.us_market_adapter import (
        fetch_us_analyst_detail, fetch_us_fundamentals_daily, fetch_us_meta, fetch_us_profile,
    )
    data: dict = {}
    try:
        data["profile"] = fetch_us_profile(ticker) or {}
    except Exception as ex:  # noqa: BLE001
        data["profile"] = {"error": str(ex)[:80]}
    try:
        data["analyst"] = fetch_us_analyst_detail(ticker)
    except Exception as ex:  # noqa: BLE001
        data["analyst"] = {"error": str(ex)[:80]}
    try:
        data["accuracy"] = _compute_accuracy(ticker)  # None 表示样本不足/无覆盖
    except Exception:  # noqa: BLE001
        data["accuracy"] = None
    try:
        data["fundamentals"] = fetch_us_fundamentals_daily(ticker) or {}
    except Exception as ex:  # noqa: BLE001
        data["fundamentals"] = {"error": str(ex)[:80]}
    try:
        m = fetch_us_meta(ticker)
        if m:
            data["description"] = m.get("description")
            data["meta_name"] = m.get("name")
    except Exception:  # noqa: BLE001
        pass
    return data


_profile_locks: dict[str, threading.Lock] = {}
_profile_locks_guard = threading.Lock()


def _get_us_profile(ticker: str, ttl_hours: float = 20.0) -> dict:
    """缓存优先:us_profile 新鲜(TTL内)直接返回;过期/缺失则现抓并回写。

    抓取失败时回退到旧缓存(哪怕过期),避免限流导致整块空白。每 ticker 串行去重,
    避免同时点开重复拉 yfinance。
    """
    import datetime as dt

    try:
        conn = _rw_conn()
    except sqlite3.OperationalError:
        return _build_profile(ticker)
    try:
        _ensure_profile_table(conn)
        row = conn.execute("SELECT data, updated_at FROM us_profile WHERE ticker=?",
                            (ticker,)).fetchone()
    finally:
        conn.close()

    cached, cached_at = (row["data"], row["updated_at"]) if row else (None, None)
    fresh = False
    if cached_at:
        try:
            age = (dt.datetime.now() - dt.datetime.fromisoformat(cached_at)).total_seconds()
            fresh = age < ttl_hours * 3600
        except ValueError:
            fresh = False
    if fresh and cached:
        try:
            d = json.loads(cached)
            d["_cached_at"] = cached_at
            return d
        except (TypeError, ValueError):
            pass

    # 需刷新:每 ticker 串行,拿不到锁说明别的请求正在刷,直接用旧缓存
    with _profile_locks_guard:
        lock = _profile_locks.setdefault(ticker, threading.Lock())
    if not lock.acquire(blocking=False):
        if cached:
            try:
                return json.loads(cached)
            except (TypeError, ValueError):
                pass
        return _build_profile(ticker)
    try:
        now_iso = dt.datetime.now().isoformat()
        data = _build_profile(ticker)
        # 抓取整体失败(profile/analyst 都报错)且有旧缓存 → 用旧缓存
        prof_err = isinstance(data.get("profile"), dict) and data["profile"].get("error")
        if prof_err and cached:
            try:
                d = json.loads(cached)
                d["_cached_at"] = cached_at
                return d
            except (TypeError, ValueError):
                pass
        try:
            conn = _rw_conn()
            try:
                _ensure_profile_table(conn)
                conn.execute(
                    "INSERT OR REPLACE INTO us_profile(ticker, data, updated_at) VALUES(?,?,?)",
                    (ticker, json.dumps(data, ensure_ascii=False), now_iso),
                )
                conn.commit()
            finally:
                conn.close()
        except sqlite3.OperationalError:
            pass
        data["_cached_at"] = now_iso
        return data
    finally:
        lock.release()


# 同一 ticker 并发首刷去重(避免多次点开同时拉 yfinance max)
_ensuring: set[str] = set()
_ensure_lock = threading.Lock()


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
        # 最新交易日须按「个股池」算:共享表里 ETF/加密/外汇(周末交易)会顶高全表 MAX,
        # 导致按全表最晚日期 JOIN 个股为空。
        latest = conn.execute(
            "SELECT MAX(q.trade_date) FROM us_daily_quotes q "
            "JOIN us_stocks s ON s.ticker=q.ticker WHERE s.is_active=1"
        ).fetchone()[0]
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
def us_detail(ticker: str):
    """单只美股详情:本地行情(近120日)+ 缓存的基本面/分析师(含详细评级分布与逐家动作)+ 简介。

    profile/分析师/基本面走 us_profile 缓存(TTL 20h),当天重复访问秒开、免 yfinance 限流。
    sync def:FastAPI 用线程池跑,缓存 miss 时的抓取阻塞不冻结事件循环。
    """
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
    cached = _get_us_profile(ticker)
    result["profile"] = cached.get("profile") or {}
    result["analyst"] = cached.get("analyst") or {}
    result["accuracy"] = cached.get("accuracy")
    result["fundamentals"] = cached.get("fundamentals") or {}
    result["description"] = cached.get("description")
    result["cached_at"] = cached.get("_cached_at")
    if not result["name"]:
        result["name"] = cached.get("meta_name")
    return result
