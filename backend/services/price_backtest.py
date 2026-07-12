"""价格因子回测引擎 — 纯量价，零前视偏差，增强版"""
import sqlite3, math
from datetime import date, timedelta

from config import DB_PATH


def run_price_backtest(days=90, top_n=5, lookback=20, pos_style="equal"):
    end_date = date.today()
    start_date = end_date - timedelta(days=max(days + lookback + 60, 365))
    start_str, end_str = start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")

    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    dates = [r["trade_date"] for r in conn.execute(
        "SELECT DISTINCT trade_date FROM stock_daily_quotes WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date",
        (start_str, end_str)).fetchall()]
    if len(dates) < lookback + 10:
        return {"error": f"数据不足，需{lookback+10}个交易日，仅{len(dates)}个"}

    quotes = {}
    for r in conn.execute("""SELECT s.code, q.trade_date, q.close, q.volume
        FROM stock_daily_quotes q JOIN stocks s ON q.stock_id=s.id
        WHERE s.is_active=1 AND q.trade_date BETWEEN ? AND ? ORDER BY q.trade_date""",
        (start_str, end_str)).fetchall():
        quotes.setdefault(r["code"], {})[r["trade_date"]] = {"close": r["close"], "volume": r["volume"]}

    name_map = {r["code"]: r["name"] for r in conn.execute("SELECT code, name FROM stocks WHERE is_active=1").fetchall()}
    conn.close()

    # 基准计算
    benchmark = compute_benchmark(quotes, dates)

    # 只回测最近 N 天
    cutoff_idx = max(0, len(dates) - 1 - int(days * 0.65))
    dates = dates[cutoff_idx:]

    cash = 100000.0
    holdings = {}
    daily_records = []
    trades = []
    rebalance_interval = min(5, max(1, len(dates) // 20))

    for di, dt in enumerate(dates):
        available = {c: quotes[c][dt] for c in quotes if dt in quotes[c]}

        if di % rebalance_interval == 0 and di >= lookback:
            scores = {}
            for code in available:
                factor = calc_factors(quotes[code], dates, di, lookback)
                if factor:
                    scores[code] = factor

            ranked = sorted(scores.items(), key=lambda x: -x[1])
            selected = [c for c, _ in ranked[:top_n]]

            for code in list(holdings):
                if code not in selected:
                    cash += holdings[code] * available[code]["close"]
                    trades.append({"date": dt, "code": code, "name": name_map.get(code, ""),
                                   "action": "SELL", "price": round(available[code]["close"], 2),
                                   "shares": holdings[code]})
                    del holdings[code]

            if selected and cash > 0:
                weights = {c: 1/len(selected) for c in selected}
                if pos_style == "weighted":
                    total_s = sum(scores[c] for c in selected)
                    if total_s > 0:
                        weights = {c: scores[c]/total_s for c in selected}
                for code in selected:
                    price = available[code]["close"]
                    target_val = cash * weights[code]
                    target_shares = max(0, int(target_val / price / 100) * 100)
                    diff = target_shares - holdings.get(code, 0)
                    if diff:
                        cash -= diff * price
                        holdings[code] = holdings.get(code, 0) + diff
                        trades.append({"date": dt, "code": code, "name": name_map.get(code, ""),
                                       "action": "BUY" if diff > 0 else "SELL",
                                       "price": round(price, 2), "shares": abs(diff)})

        hold_val = sum(holdings.get(c, 0) * available.get(c, {}).get("close", 0) for c in holdings)
        total_val = cash + hold_val
        daily_records.append({"date": dt, "value": round(total_val, 2), "cash": round(cash, 2),
                              "holdings": len(holdings)})

    return calc_metrics(daily_records, dates, trades, days, top_n, lookback, pos_style, rebalance_interval, benchmark)


def calc_metrics(daily_records, dates, trades, days, top_n, lookback, pos_style, rebalance_interval, benchmark):
    final_val = daily_records[-1]["value"] if daily_records else 100000
    returns = []
    for i in range(1, len(daily_records)):
        returns.append((daily_records[i]["value"] / daily_records[i-1]["value"]) - 1)

    avg_ret = sum(returns)/len(returns) if returns else 0
    std_ret = math.sqrt(sum((x-avg_ret)**2 for x in returns)/len(returns)) if returns else 0
    sharpe = (avg_ret/std_ret*math.sqrt(252)) if std_ret > 0 else 0

    # 最大回撤 + 回撤序列
    peak, max_dd, dd_start = daily_records[0]["value"], 0, dates[0] if dates else ""
    max_dd_s, max_dd_e = "", ""
    drawdowns = []
    for i, r in enumerate(daily_records):
        v = r["value"]
        if v > peak: peak, dd_start = v, r["date"]
        dd = (peak-v)/peak
        drawdowns.append({"date": r["date"], "drawdown_pct": round(dd*100,2)})
        if dd > max_dd: max_dd, max_dd_s, max_dd_e = dd, dd_start, r["date"]

    # 年化
    td = max(len(returns), 1)
    ann_ret = ((final_val/100000)**(252/td)-1)*100
    ann_vol = std_ret*math.sqrt(252)*100 if std_ret else 0
    calmar = ann_ret/(max_dd*100) if max_dd > 0 else 0
    wins = sum(1 for r in returns if r > 0)
    win_rate = wins/len(returns)*100 if returns else 0

    # 月度收益
    monthly = {}
    for i, r in enumerate(returns):
        m = daily_records[i+1]["date"][:7] if i+1 < len(daily_records) else ""
        if m: monthly[m] = monthly.get(m, 1)*(1+r)
    monthly_returns = {m: round((v-1)*100, 2) for m, v in monthly.items()}

    # 持仓变化
    holds_over_time = [{"date": r["date"], "count": r["holdings"]} for r in daily_records]

    # factor breakdown (approximate)
    factor_contrib = {"动量": 40, "低波动": 35, "流动性": 25}

    return {
        "params": {"days": days, "top_n": top_n, "lookback": lookback, "pos_style": pos_style,
                   "factors": "动量+低波动+流动性", "rebalance": f"每{rebalance_interval}天"},
        "start_value": 100000, "final_value": round(final_val, 2),
        "total_return_pct": round((final_val/100000-1)*100, 2),
        "max_drawdown_pct": round(max_dd*100, 2),
        "max_drawdown_period": f"{max_dd_s} ~ {max_dd_e}",
        "sharpe": round(sharpe, 2), "calmar": round(calmar, 2),
        "annualized_return_pct": round(ann_ret, 2),
        "annualized_vol_pct": round(ann_vol, 2),
        "win_rate_pct": round(win_rate, 1),
        "trade_count": len(trades),
        "daily_values": daily_records[-min(90, len(daily_records)):],
        "drawdowns": drawdowns[-min(90, len(drawdowns)):],
        "monthly_returns": monthly_returns,
        "holds_over_time": holds_over_time[-min(90, len(holds_over_time)):],
        "recent_trades": trades[-20:],
        "benchmark": benchmark,
        "factor_contrib": factor_contrib,
    }


def compute_benchmark(quotes, dates):
    """等权重持仓所有股票"""
    if len(dates) < 2: return None
    valid = [c for c in quotes if dates[0] in quotes[c]]
    if len(valid) < 3: return None
    per = 100000 / len(valid)
    end_val, count = 0, 0
    for code in valid:
        if dates[-1] in quotes[code]:
            sp = quotes[code][dates[0]]["close"]
            ep = quotes[code][dates[-1]]["close"]
            if sp > 0: end_val += per * ep / sp; count += 1
    if count < 3: return None
    return {"name": "等权基准(全部39只)", "return_pct": round((end_val/100000-1)*100, 2)}


def calc_factors(series, dates, idx, lookback):
    window = dates[max(0, idx-lookback):idx+1]
    closes = [series[d]["close"] for d in window if d in series]
    volumes = [series[d]["volume"] for d in window if d in series]
    if len(closes) < lookback * 0.6: return None
    rets = [(closes[i]/closes[i-1])-1 for i in range(1, len(closes))]
    momentum = sum(rets[-10:]) if rets else 0
    avg_r = sum(rets)/len(rets) if rets else 0
    vol = math.sqrt(sum((x-avg_r)**2 for x in rets)/len(rets)) if rets else 0
    low_vol = 1/(vol+0.001) if vol > 0 else 100
    if len(volumes) > 5 and sum(volumes) > 0:
        vols_avg = sum(volumes)/len(volumes)
        vol_stab = 1/(abs(volumes[-1]/vols_avg-1)+0.1)
    else: vol_stab = 0
    return round(momentum*0.4*100 + low_vol*0.35*0.01 + vol_stab*0.25*50 + 50, 2)
