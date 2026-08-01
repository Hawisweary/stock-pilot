"""模拟盘成交价 — 盘中实时 / 收盘 EOD / 滑点 / 涨跌停 / 交易日"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal, Optional

import config

try:
    from zoneinfo import ZoneInfo
    CN_TZ = ZoneInfo("Asia/Shanghai")
except Exception:
    from datetime import timezone, timedelta
    CN_TZ = timezone(timedelta(hours=8))
SLIPPAGE_PCT = float(os.getenv("AFR_TRADE_SLIPPAGE", "0.001"))
STALE_QUOTE_DAYS = int(os.getenv("AFR_STALE_QUOTE_DAYS", "3"))


def _relax_session() -> bool:
    return os.getenv("AFR_PORTFOLIO_RELAX_SESSION", "").lower() in ("1", "true", "yes")

PriceSource = Literal["realtime", "eod_close", "open"]
PriceMode = Literal["auto", "open", "eod_close"]
MarketMode = Literal["intraday", "eod", "closed"]


@dataclass
class MarketContext:
    now: datetime
    calendar_date: str
    latest_quote_date: str
    mode: MarketMode
    can_trade: bool
    block_reason: Optional[str]
    session_label: str


@dataclass
class TradeQuote:
    price: float
    raw_price: float
    quote_date: str
    source: PriceSource
    market_mode: MarketMode
    slippage_pct: float
    trade_date: str
    limit_up: Optional[float]
    limit_down: Optional[float]
    label: str
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "price": self.price,
            "raw_price": self.raw_price,
            "quote_date": self.quote_date,
            "source": self.source,
            "market_mode": self.market_mode,
            "slippage_pct": self.slippage_pct,
            "trade_date": self.trade_date,
            "limit_up": self.limit_up,
            "limit_down": self.limit_down,
            "label": self.label,
            "error": self.error,
        }


def _now_cn() -> datetime:
    return datetime.now(CN_TZ)


def _is_weekday(d: date) -> bool:
    from services.trade_calendar import is_trading_day
    return is_trading_day(d)


def _session_bounds(d: date) -> tuple[datetime, datetime, datetime, datetime]:
    return (
        datetime.combine(d, time(9, 30), CN_TZ),
        datetime.combine(d, time(11, 30), CN_TZ),
        datetime.combine(d, time(13, 0), CN_TZ),
        datetime.combine(d, time(15, 0), CN_TZ),
    )


def _latest_quote_date(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT MAX(trade_date) FROM stock_daily_quotes WHERE close IS NOT NULL"
    ).fetchone()
    if row and row[0]:
        return row[0]
    return _now_cn().date().isoformat()


def get_market_context(conn: Optional[sqlite3.Connection] = None) -> MarketContext:
    now = _now_cn()
    cal = now.date()
    cal_str = cal.isoformat()
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(config.DB_PATH)
        close_conn = True
    latest = _latest_quote_date(conn)
    if close_conn:
        conn.close()

    if _relax_session():
        return MarketContext(
            now, cal_str, latest, "eod", True, None, "测试模式（任意时段可成交，EOD 价）"
        )

    if not _is_weekday(cal):
        return MarketContext(
            now, cal_str, latest, "closed", False, "非交易日不可交易", "休市"
        )

    am_open, am_close, pm_open, pm_close = _session_bounds(cal)

    if now < am_open:
        return MarketContext(
            now, cal_str, latest, "closed", False,
            "开盘前不可交易（9:30 起）", "开盘前",
        )
    if am_close <= now < pm_open:
        return MarketContext(
            now, cal_str, latest, "closed", False,
            "午间休市（11:30–13:00）", "午间休市",
        )
    if now >= pm_close:
        return MarketContext(
            now, cal_str, latest, "eod", True, None,
            f"收盘后 EOD（{cal_str}）",
        )
    return MarketContext(
        now, cal_str, latest, "intraday", True, None, "盘中 · 实时价成交",
    )


def seconds_until_next_open(ctx: MarketContext | None = None) -> float:
    """距离下一次开盘/复盘还有多少秒。intraday 时返回 0。

    用于收盘后把行情类缓存 TTL 动态延长到"下次开盘前"，而不是固定短 TTL 反复
    重新拉取不会变化的收盘数据。交易日判断走本地 trade_calendar（含法定节假日），
    未同步过日历时退化为只跳过周末。
    """
    ctx = ctx or get_market_context()
    if ctx.mode == "intraday":
        return 0.0
    now = ctx.now
    d = now.date()
    if ctx.session_label == "午间休市":
        _, _, pm_open, _ = _session_bounds(d)
        return max(60.0, (pm_open - now).total_seconds())
    if ctx.session_label == "开盘前":
        am_open, _, _, _ = _session_bounds(d)
        return max(60.0, (am_open - now).total_seconds())
    next_day = d + timedelta(days=1)
    while not _is_weekday(next_day):
        next_day += timedelta(days=1)
    next_open = datetime.combine(next_day, time(9, 30), CN_TZ)
    return max(60.0, (next_open - now).total_seconds())


def get_effective_trade_date(ctx: MarketContext) -> str:
    if ctx.mode == "intraday":
        return ctx.calendar_date
    if ctx.mode == "eod" and _is_weekday(ctx.now.date()):
        return ctx.calendar_date
    return ctx.latest_quote_date


def apply_slippage(raw: float, action: str) -> float:
    if action == "buy":
        return round(raw * (1 + SLIPPAGE_PCT), 4)
    return round(raw * (1 - SLIPPAGE_PCT), 4)


def check_limit(action: str, exec_price: float, limit_up: float, limit_down: float) -> Optional[str]:
    eps = 0.01
    if limit_up > 0 and action == "buy" and exec_price >= limit_up - eps:
        return "涨停价附近无法买入"
    if limit_down > 0 and action == "sell" and exec_price <= limit_down + eps:
        return "跌停价附近无法卖出"
    return None


def _days_between(a: str, b: str) -> int:
    try:
        return abs((date.fromisoformat(a[:10]) - date.fromisoformat(b[:10])).days)
    except ValueError:
        return 0


def _get_open_price(
    conn: sqlite3.Connection, stock_id: int, prefer_date: Optional[str] = None
) -> Optional[tuple[float, str]]:
    if prefer_date:
        row = conn.execute(
            """SELECT open, trade_date FROM stock_daily_quotes
               WHERE stock_id=? AND trade_date=? AND open IS NOT NULL AND open > 0""",
            (stock_id, prefer_date),
        ).fetchone()
        if row and row[0] is not None:
            return float(row[0]), row[1]
    row = conn.execute(
        """SELECT open, trade_date FROM stock_daily_quotes
           WHERE stock_id=? AND open IS NOT NULL AND open > 0
           ORDER BY trade_date DESC LIMIT 1""",
        (stock_id,),
    ).fetchone()
    if not row or row[0] is None:
        return None
    return float(row[0]), row[1]


def _get_eod_close(
    conn: sqlite3.Connection, stock_id: int, prefer_date: Optional[str] = None
) -> Optional[tuple[float, str]]:
    if prefer_date:
        row = conn.execute(
            """SELECT close, trade_date FROM stock_daily_quotes
               WHERE stock_id=? AND trade_date=? AND close IS NOT NULL""",
            (stock_id, prefer_date),
        ).fetchone()
        if row and row[0] is not None:
            return float(row[0]), row[1]
    row = conn.execute(
        """SELECT close, trade_date FROM stock_daily_quotes
           WHERE stock_id=? AND close IS NOT NULL ORDER BY trade_date DESC LIMIT 1""",
        (stock_id,),
    ).fetchone()
    if not row or row[0] is None:
        return None
    return float(row[0]), row[1]


def _fetch_realtime(code: str) -> Optional[dict]:
    try:
        from services.data_sources import tencent_quote

        q = tencent_quote([code]).get(code)
        if not q:
            return None
        price = float(q.get("price") or 0)
        if price <= 0:
            return None
        return q
    except Exception:
        return None


def _stale_error(quote_date: str, latest: str) -> Optional[str]:
    gap = _days_between(quote_date, latest)
    if gap > STALE_QUOTE_DAYS:
        return f"行情过期（{quote_date}，库内最新 {latest}）"
    return None


def resolve_trade_price(
    code: str,
    stock_id: int,
    action: str,
    conn: Optional[sqlite3.Connection] = None,
    *,
    for_display: bool = False,
    ctx: Optional[MarketContext] = None,
    realtime_cache: Optional[dict[str, dict]] = None,
    price_mode: PriceMode = "auto",
    as_of_trade_date: Optional[str] = None,
) -> TradeQuote:
    """解析成交价。for_display=True 时仅估值/预览，不拦截交易时段。"""
    action = action.lower().strip()
    external = conn is not None
    if conn is None:
        conn = sqlite3.connect(config.DB_PATH)
    ctx = ctx or get_market_context(conn)

    allow_off_session = price_mode == "open" and bool(as_of_trade_date)
    if not for_display and not ctx.can_trade and not allow_off_session:
        return TradeQuote(
            0, 0, "", "eod_close", ctx.mode, 0, "", None, None,
            ctx.session_label, ctx.block_reason,
        )

    trade_date = as_of_trade_date or get_effective_trade_date(ctx)
    limit_up = limit_down = None
    raw = 0.0
    quote_date = ""
    source: PriceSource = "eod_close"

    use_open = price_mode == "open"
    use_intraday = (
        price_mode == "auto"
        and ctx.mode == "intraday"
        and not _relax_session()
    )
    if _relax_session():
        use_intraday = False

    rt: Optional[dict] = None
    if use_open:
        prefer = as_of_trade_date or ctx.calendar_date
        opened = _get_open_price(conn, stock_id, prefer_date=prefer)
        if opened:
            raw, quote_date = opened
            source = "open"
        else:
            rt = (realtime_cache or {}).get(code) if realtime_cache is not None else None
            if rt is None and prefer == ctx.calendar_date:
                rt = _fetch_realtime(code)
            if rt:
                open_px = float(rt.get("open") or 0)
                if open_px > 0:
                    raw = open_px
                    quote_date = prefer
                    source = "open"
                    limit_up = float(rt.get("limit_up") or 0) or None
                    limit_down = float(rt.get("limit_down") or 0) or None
        if raw <= 0:
            eod = _get_eod_close(conn, stock_id, prefer_date=prefer)
            if eod:
                raw, quote_date = eod
                source = "eod_close"
    elif use_intraday:
        rt = (realtime_cache or {}).get(code) if realtime_cache is not None else None
        if rt is None:
            rt = _fetch_realtime(code)
        if rt:
            raw = float(rt["price"])
            quote_date = ctx.calendar_date
            source = "realtime"
            limit_up = float(rt.get("limit_up") or 0) or None
            limit_down = float(rt.get("limit_down") or 0) or None
            if rt.get("price", 0) <= 0:
                return TradeQuote(
                    0, 0, quote_date, source, ctx.mode, SLIPPAGE_PCT, trade_date,
                    limit_up, limit_down, "停牌或无行情", "停牌或无实时行情",
                )

    if raw <= 0 and not use_open:
        prefer = ctx.calendar_date if ctx.mode == "eod" and _is_weekday(ctx.now.date()) else None
        if price_mode == "eod_close" and as_of_trade_date:
            prefer = as_of_trade_date
        eod = _get_eod_close(conn, stock_id, prefer_date=prefer)
        if not eod:
            if not external:
                conn.close()
            return TradeQuote(
                0, 0, "", "eod_close", ctx.mode, 0, trade_date, None, None,
                "无行情", "无行情数据，无法成交",
            )
        raw, quote_date = eod
        source = "eod_close"
        stale = _stale_error(quote_date, ctx.latest_quote_date)
        if stale and not for_display:
            if not external:
                conn.close()
            return TradeQuote(
                0, raw, quote_date, source, ctx.mode, 0, trade_date, None, None,
                f"EOD {quote_date}", stale,
            )
        if not use_intraday and rt is None and not for_display:
            rt = _fetch_realtime(code)
        if rt:
            limit_up = float(rt.get("limit_up") or 0) or None
            limit_down = float(rt.get("limit_down") or 0) or None

    exec_price = apply_slippage(raw, action) if not for_display else raw
    if not for_display:
        lim_err = check_limit(action, exec_price, limit_up or 0, limit_down or 0)
        if lim_err:
            if not external:
                conn.close()
            return TradeQuote(
                exec_price, raw, quote_date, source, ctx.mode, SLIPPAGE_PCT, trade_date,
                limit_up, limit_down, _price_label(source, quote_date, ctx), lim_err,
            )

    label = _price_label(source, quote_date, ctx, raw, exec_price if not for_display else raw, action)
    if not external:
        conn.close()
    return TradeQuote(
        exec_price if not for_display else raw,
        raw,
        quote_date,
        source,
        ctx.mode,
        SLIPPAGE_PCT if not for_display else 0,
        trade_date,
        limit_up,
        limit_down,
        label,
    )


def _price_label(
    source: PriceSource,
    quote_date: str,
    ctx: MarketContext,
    raw: float = 0,
    exec_price: float = 0,
    action: str = "buy",
) -> str:
    if source == "realtime":
        slip = f" · 含滑点 {SLIPPAGE_PCT * 100:.2f}%" if exec_price and raw and exec_price != raw else ""
        return f"实时价 ¥{exec_price or raw}{slip}"
    if source == "open":
        slip = f" · 含滑点 {SLIPPAGE_PCT * 100:.2f}%" if exec_price and raw and exec_price != raw else ""
        return f"开盘价 ¥{exec_price or raw}（{quote_date}）{slip}"
    return f"收盘价 ¥{exec_price or raw}（{quote_date}）"


def resolve_mark_prices(
    codes: list[tuple[str, int]],
    conn: Optional[sqlite3.Connection] = None,
    rt_cache: Optional[dict[str, dict]] = None,
) -> dict[str, float]:
    """持仓市值 — 优先腾讯实时价，否则库内 EOD"""
    return {
        code: meta["price"]
        for code, meta in resolve_mark_prices_with_meta(codes, conn, rt_cache=rt_cache).items()
    }


def resolve_mark_prices_with_meta(
    codes: list[tuple[str, int]],
    conn: Optional[sqlite3.Connection] = None,
    rt_cache: Optional[dict[str, dict]] = None,
) -> dict[str, dict]:
    """持仓市值（含来源）— 展示用始终先试实时，避免库内 EOD 滞后

    rt_cache: 调用方可预先批量拉好的实时行情缓存（跨多个组合共用一次网络请求）。
    传 None 时退回单次调用 tencent_quote（保持向后兼容）。
    """
    external = conn is not None
    if conn is None:
        conn = sqlite3.connect(config.DB_PATH)
    ctx = get_market_context(conn)
    out: dict[str, dict] = {}
    if rt_cache is None:
        try:
            from services.data_sources import tencent_quote

            rt_cache = tencent_quote([c for c, _ in codes])
        except Exception:
            rt_cache = {}

    for code, sid in codes:
        rt = rt_cache.get(code)
        if rt:
            live = float(rt.get("price") or 0)
            if live > 0:
                out[code] = {
                    "price": live,
                    "source": "realtime",
                    "quote_date": ctx.calendar_date,
                }
                continue
        prefer = ctx.calendar_date if ctx.mode == "eod" and _is_weekday(ctx.now.date()) else None
        eod = _get_eod_close(conn, sid, prefer_date=prefer)
        if eod:
            out[code] = {"price": eod[0], "source": "eod_close", "quote_date": eod[1]}
        else:
            out[code] = {"price": 0.0, "source": "eod_close", "quote_date": ""}

    if not external:
        conn.close()
    return out


def pricing_context_dict(conn: Optional[sqlite3.Connection] = None) -> dict:
    external = conn is not None
    if conn is None:
        conn = sqlite3.connect(config.DB_PATH)
    ctx = get_market_context(conn)
    if not external:
        conn.close()
    return {
        "mode": ctx.mode,
        "can_trade": ctx.can_trade,
        "block_reason": ctx.block_reason,
        "session_label": ctx.session_label,
        "calendar_date": ctx.calendar_date,
        "latest_quote_date": ctx.latest_quote_date,
        "trade_date": get_effective_trade_date(ctx),
        "slippage_pct": SLIPPAGE_PCT,
        "rules": {
            "intraday": "9:30–11:30 / 13:00–15:00 按实时价 + 滑点成交",
            "eod": "15:00 后按当日收盘价（未入库则用最近 EOD）",
            "next_open": "自动调仓/海龟出场：收盘生成计划，下一交易日开盘价 + 滑点成交",
            "limits": "涨停不可买、跌停不可卖",
        },
    }
