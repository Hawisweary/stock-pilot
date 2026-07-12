"""Polars 加速层 — 替换 pandas 热点路径，零拷贝 Arrow 转换"""
import sqlite3

from config import DB_PATH, USE_POLARS


def load_quotes_polars(stock_id: int = None, days: int = 365) -> "pl.DataFrame":
    """零拷贝加载日线数据 → Polars DataFrame"""
    if USE_POLARS:
        from services.data_bridge import read_quotes_polars

        df = read_quotes_polars(stock_id=stock_id, days=days)
        if df is not None:
            return df

    import polars as pl
    import pyarrow as pa

    conn = sqlite3.connect(DB_PATH)
    if stock_id:
        rows = conn.execute("""SELECT trade_date, open, high, low, close, volume
            FROM stock_daily_quotes WHERE stock_id=? AND close IS NOT NULL
            ORDER BY trade_date DESC LIMIT ?""", (stock_id, days)).fetchall()
    else:
        rows = conn.execute("""SELECT s.code, q.trade_date, q.open, q.high, q.low, q.close, q.volume
            FROM stock_daily_quotes q JOIN stocks s ON q.stock_id=s.id
            WHERE s.is_active=1 AND q.close IS NOT NULL
            ORDER BY q.trade_date DESC LIMIT ?""", (days * 39,)).fetchall()
    conn.close()
    if not rows: return pl.DataFrame()
    # Arrow 零拷贝
    cols = ["date", "open", "high", "low", "close", "volume"] if stock_id else ["code", "date", "open", "high", "low", "close", "volume"]
    arrays = [pa.array([r[i] for r in rows]) for i in range(len(cols))]
    table = pa.table(dict(zip(cols, arrays)))
    return pl.from_arrow(table).sort("date")


def calc_indicators_polars(df) -> "pl.DataFrame":
    """批量计算 MACD/RSI/KDJ/BOLL — Vectorized"""
    import polars as pl

    df = df.with_columns(pl.col("close").cast(pl.Float64))
    # MA
    df = df.with_columns([
        pl.col("close").rolling_mean(5).alias("ma5"),
        pl.col("close").rolling_mean(10).alias("ma10"),
        pl.col("close").rolling_mean(20).alias("ma20"),
    ])
    # RSI(14)
    delta = df["close"].diff()
    gain = delta.clip(lower_bound=0).rolling_mean(14)
    loss = (-delta).clip(lower_bound=0).rolling_mean(14)
    rs = gain / (loss + 1e-10)
    df = df.with_columns((100 - 100/(1+rs)).alias("rsi14"))
    # MACD
    ema12 = df["close"].ewm_mean(span=12, min_periods=12)
    ema26 = df["close"].ewm_mean(span=26, min_periods=26)
    dif = ema12 - ema26
    dea = dif.ewm_mean(span=9, min_periods=9)
    macd_bar = 2 * (dif - dea)
    df = df.with_columns([
        pl.Series("macd_dif", dif), pl.Series("macd_dea", dea), pl.Series("macd_bar", macd_bar),
    ])
    # BOLL
    boll_mid = df["close"].rolling_mean(20)
    boll_std = df["close"].rolling_std(20)
    df = df.with_columns([
        boll_mid.alias("boll_mid"),
        (boll_mid + 2 * boll_std).alias("boll_upper"),
        (boll_mid - 2 * boll_std).alias("boll_lower"),
    ])
    # KDJ
    low_n = df["low"].rolling_min(9)
    high_n = df["high"].rolling_max(9)
    rsv = (df["close"] - low_n) / (high_n - low_n + 1e-10) * 100
    k = rsv.ewm_mean(span=3, min_periods=3)
    d_j = k.ewm_mean(span=3, min_periods=3)
    j = 3 * k - 2 * d_j
    df = df.with_columns([
        pl.Series("kdj_k", k), pl.Series("kdj_d", d_j), pl.Series("kdj_j", j),
    ])
    return df


def compute_capital_flow_polars(stock_id: int) -> dict:
    """资金面评分 — Polars 加速"""
    df = load_quotes_polars(stock_id, 120)
    if df.is_empty(): return {"stock_id": stock_id, "score": 50, "details": {}}
    df = df.sort("date").tail(60)
    closes = df["close"]
    volumes = df["volume"]
    # 量价关系
    price_changes = closes.pct_change().tail(20)
    vol_ratio = volumes.tail(5).mean() / (volumes.tail(20).mean() + 1)
    up_days = (price_changes > 0).sum()
    down_days = (price_changes < 0).sum()
    momentum = price_changes.sum()
    score = 50 + momentum * 100 * 0.5 + (up_days - down_days) * 2
    score = max(0, min(100, score))
    return {"stock_id": stock_id, "score": round(score, 1),
            "details": {"vol_ratio": round(vol_ratio, 2), "up_days": up_days, "momentum": round(momentum * 100, 2)}}
