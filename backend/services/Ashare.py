# -*- coding: utf-8 -*-
"""Ashare 股票行情 — 新浪主力 + 腾讯备用（https / QQ 代理，避免 http→https 重定向 SSL 失败）"""
from __future__ import annotations

import datetime
import json
import time
from typing import Any

import pandas as pd

from services.http_client import get as http_get


def _fetch_json(urls: list[str], *, retries: int = 1) -> dict[str, Any]:
    last_err: Exception | None = None
    for url in urls:
        for attempt in range(retries + 1):
            try:
                resp = http_get(url, timeout=20)
                resp.raise_for_status()
                return json.loads(resp.content)
            except Exception as e:
                last_err = e
                if attempt < retries:
                    time.sleep(0.4 * (attempt + 1))
    if last_err:
        raise last_err
    raise RuntimeError("行情请求失败")


def _tx_day_urls(code: str, unit: str, end_date: str, count: int) -> list[str]:
    param = f"{code},{unit},,{end_date},{count},qfq"
    return [
        f"https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param={param}",
        f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={param}",
    ]


def get_price_day_tx(code, end_date="", count=10, frequency="1d"):
    unit = "week" if frequency in "1w" else "month" if frequency in "1M" else "day"
    if end_date:
        end_date = (
            end_date.strftime("%Y-%m-%d")
            if isinstance(end_date, datetime.date)
            else end_date.split(" ")[0]
        )
    if end_date == datetime.datetime.now().strftime("%Y-%m-%d"):
        end_date = ""

    st = _fetch_json(_tx_day_urls(code, unit, end_date, count))
    ms = "qfq" + unit
    stk = st["data"][code]
    buf = stk[ms] if ms in stk else stk[unit]
    rows = [row[:6] for row in buf]
    df = pd.DataFrame(rows, columns=["time", "open", "close", "high", "low", "volume"])
    for col in ["open", "close", "high", "low", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.time = pd.to_datetime(df.time)
    df.set_index(["time"], inplace=True)
    df.index.name = ""
    return df


def get_price_min_tx(code, end_date=None, count=10, frequency="1d"):
    ts = int(frequency[:-1]) if frequency[:-1].isdigit() else 1
    if end_date:
        end_date = (
            end_date.strftime("%Y-%m-%d")
            if isinstance(end_date, datetime.date)
            else end_date.split(" ")[0]
        )
    param = f"{code},m{ts},,{count}"
    st = _fetch_json([
        f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={param}",
        f"https://proxy.finance.qq.com/ifzqgtimg/appstock/app/kline/mkline?param={param}",
    ])
    buf = st["data"][code]["m" + str(ts)]
    df = pd.DataFrame(buf, columns=["time", "open", "close", "high", "low", "volume", "n1", "n2"])
    df = df[["time", "open", "close", "high", "low", "volume"]]
    df[["open", "close", "high", "low", "volume"]] = df[
        ["open", "close", "high", "low", "volume"]
    ].astype("float")
    df.time = pd.to_datetime(df.time)
    df.set_index(["time"], inplace=True)
    df.index.name = ""
    df["close"][-1] = float(st["data"][code]["qt"][code][3])
    return df


def get_price_sina(code, end_date="", count=10, frequency="60m"):
    frequency = frequency.replace("1d", "240m").replace("1w", "1200m").replace("1M", "7200m")
    mcount = count
    ts = int(frequency[:-1]) if frequency[:-1].isdigit() else 1
    if (end_date != "") & (frequency in ["240m", "1200m", "7200m"]):
        end_date = pd.to_datetime(end_date) if not isinstance(end_date, datetime.date) else end_date
        unit = 4 if frequency == "1200m" else 29 if frequency == "7200m" else 1
        count = count + (datetime.datetime.now() - end_date).days // unit
    url = (
        f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"CN_MarketData.getKLineData?symbol={code}&scale={ts}&ma=5&datalen={count}"
    )
    dstr = _fetch_json([url])
    df = pd.DataFrame(dstr, columns=["day", "open", "high", "low", "close", "volume"])
    df["open"] = df["open"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["close"] = df["close"].astype(float)
    df["volume"] = df["volume"].astype(float)
    df.day = pd.to_datetime(df.day)
    df.set_index(["day"], inplace=True)
    df.index.name = ""
    if (end_date != "") & (frequency in ["240m", "1200m", "7200m"]):
        return df[df.index <= end_date][-mcount:]
    return df


def _last_bar_date(df: pd.DataFrame | None) -> datetime.date | None:
    if df is None or df.empty:
        return None
    ts = pd.Timestamp(df.index[-1])
    return ts.date()


def _pick_fresher_ohlc(df_a: pd.DataFrame | None, df_b: pd.DataFrame | None) -> pd.DataFrame | None:
    """两路行情都成功时，取最后一根 K 线日期更新的那份（避免新浪滞后、腾讯已有当日）。"""
    if df_a is None or df_a.empty:
        return df_b
    if df_b is None or df_b.empty:
        return df_a
    da = _last_bar_date(df_a)
    db = _last_bar_date(df_b)
    if da and db and db > da:
        return df_b
    return df_a


def get_price(code, end_date="", count=10, frequency="1d", fields=None):
    xcode = code.replace(".XSHG", "").replace(".XSHE", "")
    xcode = (
        "sh" + xcode
        if "XSHG" in code
        else "sz" + xcode
        if "XSHE" in code
        else code
    )

    if frequency in ["1d", "1w", "1M"]:
        df_sina: pd.DataFrame | None = None
        df_tx: pd.DataFrame | None = None
        try:
            df_sina = get_price_sina(xcode, end_date=end_date, count=count, frequency=frequency)
        except Exception:
            pass
        try:
            df_tx = get_price_day_tx(xcode, end_date=end_date, count=count, frequency=frequency)
        except Exception:
            pass
        picked = _pick_fresher_ohlc(df_sina, df_tx)
        if picked is not None and not picked.empty:
            return picked
        if df_sina is not None and not df_sina.empty:
            return df_sina
        if df_tx is not None and not df_tx.empty:
            return df_tx
        raise RuntimeError(f"日线行情不可用: {xcode}")

    if frequency in ["1m", "5m", "15m", "30m", "60m"]:
        if frequency in "1m":
            return get_price_min_tx(xcode, end_date=end_date, count=count, frequency=frequency)
        try:
            return get_price_sina(xcode, end_date=end_date, count=count, frequency=frequency)
        except Exception:
            return get_price_min_tx(xcode, end_date=end_date, count=count, frequency=frequency)

    raise ValueError(f"unsupported frequency: {frequency}")
