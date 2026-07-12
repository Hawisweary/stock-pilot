"""多周期技术面 — 日线/周线/月线趋势共振"""
import sqlite3
from datetime import date, timedelta

from config import DB_PATH


def compute_multicyc_signal(stock_id: int, code: str) -> dict:
    """日/周/月线趋势共振信号"""
    today = date.today().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 获取日线数据
    daily = conn.execute("""
        SELECT trade_date, close FROM stock_daily_quotes
        WHERE stock_id=? AND close IS NOT NULL
        ORDER BY trade_date DESC LIMIT 250
    """, (stock_id,)).fetchall()
    daily.reverse()

    if len(daily) < 30:
        conn.close()
        return {"error": "日线数据不足"}

    prices = [r["close"] for r in daily]

    def calc_ma(values, period):
        if len(values) < period: return None
        return sum(values[-period:]) / period

    def calc_trend(values, period):
        """判断趋势：1=上升 0=横盘 -1=下降"""
        if len(values) < period: return 0
        ma = calc_ma(values, period)
        return 1 if values[-1] > ma else (-1 if values[-1] < ma else 0)

    # 日线
    d_ma5 = calc_ma(prices, 5)
    d_ma20 = calc_ma(prices, 20)
    d_trend = calc_trend(prices, 20)
    d_score = 50
    if d_trend == 1 and prices[-1] > d_ma5:
        d_score = 70  # 日线多头排列
    elif d_trend == 1:
        d_score = 60
    elif d_trend == -1 and prices[-1] < d_ma5:
        d_score = 30  # 日线空头排列
    elif d_trend == -1:
        d_score = 40

    # 周线（每5个日线为一个周）
    week_closes = []
    for i in range(0, len(prices), 5):
        chunk = prices[i:i+5]
        if chunk:
            week_closes.append(chunk[-1])

    w_trend = calc_trend(week_closes, min(13, len(week_closes)))
    w_score = 50
    if w_trend == 1:
        w_score = 70
    elif w_trend == -1:
        w_score = 30

    # 月线（每21个日线为一个月）
    month_closes = []
    for i in range(0, len(prices), 21):
        chunk = prices[i:i+21]
        if chunk:
            month_closes.append(chunk[-1])

    m_trend = calc_trend(month_closes, min(6, len(month_closes)))
    m_score = 50
    if m_trend == 1:
        m_score = 65
    elif m_trend == -1:
        m_score = 35

    # 多周期共振
    daily_dir = "↑" if d_trend == 1 else ("↓" if d_trend == -1 else "→")
    weekly_dir = "↑" if w_trend == 1 else ("↓" if w_trend == -1 else "→")
    monthly_dir = "↑" if m_trend == 1 else ("↓" if m_trend == -1 else "→")

    directions = [d_trend, w_trend, m_trend]
    if all(d == 1 for d in directions):
        signal = "三周期共振向上 → 强烈看多"
    elif all(d == -1 for d in directions):
        signal = "三周期共振向下 → 强烈看空"
    elif d_trend == 1 and w_trend == 1:
        signal = "日周共振向上 → 看多"
    elif d_trend == -1 and w_trend == -1:
        signal = "日周共振向下 → 看空"
    elif d_trend == 1 and w_trend == -1:
        signal = "日线反弹周线承压 → 谨慎"
    elif d_trend == -1 and w_trend == 1:
        signal = "日线回调周线向上 → 关注买点"
    else:
        signal = "周期信号不明确 → 观望"

    # 写入多周期评分
    composite = d_score * 0.40 + w_score * 0.35 + m_score * 0.25
    conn.execute("""CREATE TABLE IF NOT EXISTS multicyc_scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT, stock_id INTEGER NOT NULL,
        date TEXT NOT NULL, composite_score REAL, daily_score REAL,
        weekly_score REAL, monthly_score REAL, signal TEXT,
        UNIQUE(stock_id, date))""")
    conn.execute("""INSERT OR REPLACE INTO multicyc_scores
        (stock_id,date,composite_score,daily_score,weekly_score,monthly_score,signal)
        VALUES (?,?,?,?,?,?,?)""",
        (stock_id, today, round(composite, 1), d_score, w_score, m_score, signal))
    conn.commit()
    conn.close()

    return {
        "stock_id": stock_id, "code": code, "date": today,
        "composite_score": round(composite, 1),
        "daily": {"trend": daily_dir, "score": d_score, "ma20": round(d_ma20, 2) if d_ma20 else None},
        "weekly": {"trend": weekly_dir, "score": w_score},
        "monthly": {"trend": monthly_dir, "score": m_score},
        "signal": signal,
    }
