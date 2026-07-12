"""实时行情 API — 内存缓存 + 批量转发"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time

from fastapi import APIRouter, Query

from config import DB_PATH
from services.http_client import get as http_get

router = APIRouter(prefix="/api/realtime", tags=["realtime"])

_TENCENT_HEADERS = {"Referer": "https://gu.qq.com/"}
_QUOTE_CHUNK = 200
_QUOTE_TTL = 30       # 秒：行情缓存有效期
_QUOTE_CONCURRENCY = 8  # 并发批次数


def _tencent_prefix(code: str) -> str:
    return f"sz{code}" if not code.startswith(("6", "9")) else f"sh{code}"


def _fetch_tencent_json(urls: list[str]) -> dict:
    """腾讯行情 JSON / JSONP — 走 http_client 直连，避免系统代理导致 SSL 失败。"""
    last_err: Exception | None = None
    for url in urls:
        try:
            resp = http_get(url, timeout=10, headers=_TENCENT_HEADERS)
            resp.raise_for_status()
            text = resp.text.strip()
            if not text or text.lstrip().startswith("<"):
                continue
            if "=" in text and not text.lstrip().startswith("{"):
                text = text.split("=", 1)[1].strip().rstrip(";")
            return json.loads(text)
        except Exception as e:
            last_err = e
    if last_err:
        raise last_err
    raise RuntimeError("腾讯行情响应为空")


def _tencent_quote_chunk(codes: list[str]) -> dict:
    from services.data_sources import tencent_quote
    return tencent_quote(codes)


async def _fetch_quotes_concurrent(codes: list[str]) -> dict:
    """分批并发拉取腾讯行情，最多 _QUOTE_CONCURRENCY 个批次同时进行。"""
    chunks = [codes[i:i + _QUOTE_CHUNK] for i in range(0, len(codes), _QUOTE_CHUNK)]
    sem = asyncio.Semaphore(_QUOTE_CONCURRENCY)
    loop = asyncio.get_event_loop()

    async def fetch_one(chunk: list[str]) -> dict:
        async with sem:
            return await loop.run_in_executor(None, _tencent_quote_chunk, chunk)

    results = await asyncio.gather(*[fetch_one(c) for c in chunks], return_exceptions=True)
    merged: dict = {}
    for r in results:
        if isinstance(r, dict):
            merged.update(r)
    return merged


# 内存缓存（30秒TTL）
_cache: dict = {"time": 0, "data": None}

@router.get("/quotes")
async def get_realtime_quotes(market: str | None = Query(None, description="A/SH/SZ/ALL，不传返回全部")):
    """批量获取活跃股票实时行情（30秒缓存，并发拉取）"""
    now = time.time()
    cache_key = (market or "ALL").upper()

    if (
        now - _cache["time"] < _QUOTE_TTL
        and _cache["data"] is not None
        and _cache.get("market") == cache_key
    ):
        return _cache["data"]

    conn = sqlite3.connect(DB_PATH)
    # 仅拉 A 股行情（腾讯接口对 US/HK 覆盖差），如需全部可去掉 WHERE market 过滤
    if cache_key not in ("ALL", ""):
        stocks = conn.execute(
            "SELECT id, code, name FROM stocks WHERE is_active=1 AND market=?", (cache_key,)
        ).fetchall()
    else:
        stocks = conn.execute(
            "SELECT id, code, name FROM stocks WHERE is_active=1 AND market IN ('A','SH','SZ')"
        ).fetchall()
    conn.close()

    codes = [s[1] for s in stocks]
    quotes = await _fetch_quotes_concurrent(codes)

    result = []
    for s in stocks:
        q = quotes.get(s[1], {})
        result.append({
            "id": s[0], "code": s[1], "name": s[2],
            "price": q.get("price", 0),
            "change_pct": q.get("change_pct", 0),
            "change_amt": q.get("change_amt", 0),
            "volume": q.get("amount_wan", 0),
            "turnover_pct": q.get("turnover_pct", 0),
            "high": q.get("high", 0),
            "low": q.get("low", 0),
            "open": q.get("open", 0),
            "last_close": q.get("last_close", 0),
            "amplitude_pct": q.get("amplitude_pct", 0),
            "pe_ttm": q.get("pe_ttm", 0),
            "pb": q.get("pb", 0),
            "market_cap": q.get("mcap_yi", 0),
        })

    _cache["time"] = now
    _cache["data"] = result
    _cache["market"] = cache_key
    return result


@router.get("/intraday/{stock_id}")
async def get_intraday(stock_id: int):
    """当天分时图数据（单只，腾讯 minute API）"""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT code FROM stocks WHERE id=?", (stock_id,)).fetchone()
    conn.close()
    if not row:
        raise __import__("fastapi").HTTPException(status_code=404, detail="stock not found")

    code = row[0]
    prefix = _tencent_prefix(code)

    urls = [
        f"https://ifzq.gtimg.cn/appstock/app/minute/query?_var=min_data_{prefix}&code={prefix}",
        f"https://proxy.finance.qq.com/ifzqgtimg/appstock/app/minute/query?code={prefix}",
    ]
    try:
        payload = _fetch_tencent_json(urls)
        node = payload.get("data", {}).get(prefix, {})

        # 新格式: data.data = ["0930 1327.00 884 117306800.00", ...]
        raw_lines = []
        inner = node.get("data")
        if isinstance(inner, dict):
            raw_lines = inner.get("data") or []
        if not raw_lines:
            raw_lines = node.get("line") or []

        bars = []
        for item in raw_lines:
            if isinstance(item, str):
                parts = item.split()
                if len(parts) < 2:
                    continue
                t, price = parts[0], parts[1]
                vol = int(float(parts[2])) if len(parts) > 2 else 0
                bars.append({"time": t, "price": float(price), "volume": vol})
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                bars.append({
                    "time": str(item[0]),
                    "price": float(item[1]),
                    "volume": int(item[2]) if len(item) > 2 else 0,
                })

        qt = (node.get("qt") or {}).get(prefix) or []
        prev_close = float(qt[4]) if len(qt) > 4 and qt[4] not in ("", None) else None
        trade_date = inner.get("date") if isinstance(inner, dict) else None

        return {
            "code": code,
            "bars": bars,
            "count": len(bars),
            "prev_close": prev_close,
            "trade_date": trade_date,
        }
    except Exception as e:
        return {"error": str(e), "bars": [], "degraded": True}


@router.get("/kline/{stock_id}")
async def get_realtime_kline(stock_id: int, period: str = "daily", count: int = 120):
    """K线数据批量读取（日/周/月，DB存储）"""
    period_map = {"daily": "1d", "weekly": "1w", "monthly": "1m"}
    p = period_map.get(period, "1d")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    if period == "5min":
        # 5分钟K线走腾讯实时接口，不存DB
        row = conn.execute("SELECT code FROM stocks WHERE id=?", (stock_id,)).fetchone()
        conn.close()
        if not row:
            raise __import__("fastapi").HTTPException(status_code=404, detail="stock not found")
        return await _fetch_5min_kline(row[0])

    # 日/周/月从DB读取
    day_map = {"1d": 365, "1w": 365*3, "1m": 365*5}
    limit = day_map.get(p, 365)
    rows = conn.execute(
        "SELECT trade_date as date, open, high, low, close, volume FROM stock_daily_quotes "
        "WHERE stock_id=? ORDER BY trade_date DESC LIMIT ?",
        (stock_id, limit if p == "1d" else limit * 2)
    ).fetchall()
    conn.close()
    kline = [dict(r) for r in rows]
    kline.reverse()

    return {"stock_id": stock_id, "period": period, "kline": kline}


async def _fetch_5min_kline(code: str):
    """腾讯5分钟K线（实时，不落库）"""
    prefix = _tencent_prefix(code)
    param = f"{prefix},m5,,320"
    urls = [
        f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={param}",
        f"https://proxy.finance.qq.com/ifzqgtimg/appstock/app/kline/mkline?param={param}",
    ]
    try:
        data = _fetch_tencent_json(urls)
        raw = data.get("data", {}).get(prefix, {}).get("m5", [])
        bars = []
        for item in raw:
            if len(item) < 6:
                continue
            try:
                bars.append({
                    "time": str(item[0]),
                    "open": float(item[1]),
                    "close": float(item[2]),
                    "high": float(item[3]),
                    "low": float(item[4]),
                    "volume": int(float(item[5])),
                })
            except (TypeError, ValueError):
                continue
        return {"code": code, "period": "5min", "kline": bars, "count": len(bars)}
    except Exception as e:
        return {"error": str(e), "kline": [], "degraded": True}
