"""统一回测引擎 — 综合分/单因子/动量策略，含成本、T+1、基准"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from config import DB_PATH
from services.trading_rules import (
    BACKTEST_COMMISSION,
    COMMISSION,
    SLIPPAGE,
    STAMP_TAX,
    trade_allowed,
)
from services.strategies.turtle import turtle_atr, turtle_should_exit
from services.strategy_registry import effective_v5_strategy, normalize_strategy_id
from services.strategy_selector import compute_backtest_day_scores, load_dividend_yield_snap
from services.v5_score_query import load_score_snap_range, resolve_score_spec


def run_backtest(
    days: int = 90,
    top_n: int = 5,
    lookback: int = 20,
    pos_style: str = "equal",
    strategy: str = "composite",
    min_score: float = 50,
    rebalance: str = "weekly",
    apply_costs: bool = True,
    apply_t1: bool = True,
    combination_id: Optional[int] = None,
    apply_slippage_cost: bool = True,
    apply_limit_rules: bool = True,
    ml_horizon: Optional[int] = None,
    benchmark_mode: str = "equal",
    sector_window: int = 5,
    exec_mode: str = "next_open",  # "next_open"（默认 T+1 开盘）| "close"（legacy 当日收盘）
    initial_cash: float = 100_000.0,
    commission_override: float | None = None,   # 覆盖佣金率（回测专用）
    slippage_override: float | None = None,     # 覆盖滑点
    stamp_tax_override: float | None = None,    # 覆盖印花税
    risk_free_rate: float = 0.02,               # 年化无风险利率，Sharpe 用
) -> dict:
    """主回测入口。strategy: composite|turtle|sector_rotation|momentum|..."""
    strategy = normalize_strategy_id(strategy)
    bench_mode = (benchmark_mode or "equal").lower()
    if strategy == "index_enhance" and bench_mode == "equal":
        bench_mode = "csi300"
    score_strategy = effective_v5_strategy(strategy)
    end_date = date.today()
    buffer_days = max(days + lookback + 30, 120)
    start_date = end_date - timedelta(days=int(buffer_days * 1.5))
    start_str, end_str = start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    all_dates = [
        r["trade_date"]
        for r in conn.execute(
            """SELECT DISTINCT trade_date FROM stock_daily_quotes
               WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date""",
            (start_str, end_str),
        ).fetchall()
    ]
    if len(all_dates) < lookback + 10:
        conn.close()
        return {"error": f"数据不足，需至少{lookback + 10}个交易日"}

    dates = all_dates[-days:] if len(all_dates) >= days else all_dates

    quotes: Dict[str, Dict[str, dict]] = {}
    qcols = "q.close, q.volume, q.high, q.low"
    try:
        info = conn.execute("PRAGMA table_info(stock_daily_quotes)").fetchall()
        names = {r[1] for r in info}
        if "open" in names:
            qcols += ", q.open"
        if "change_pct" in names:
            qcols += ", q.change_pct"
        if "is_suspended" in names:
            qcols += ", q.is_suspended"
        if "high" not in names:
            qcols = "q.close, q.volume"
        if "low" not in names and "q.low" in qcols:
            qcols = qcols.replace(", q.low", "")
    except sqlite3.OperationalError:
        qcols = "q.close, q.volume"

    for r in conn.execute(
        f"""SELECT s.code, q.trade_date, {qcols}
           FROM stock_daily_quotes q JOIN stocks s ON q.stock_id = s.id
           WHERE s.is_active = 1 AND q.trade_date BETWEEN ? AND ?
           ORDER BY q.trade_date""",
        (start_str, end_str),
    ).fetchall():
        entry = {
            "close": float(r["close"] or 0),
            "volume": float(r["volume"] or 0),
        }
        if "open" in r.keys():
            entry["open"] = float(r["open"] or r["close"] or 0)
        if "high" in r.keys():
            entry["high"] = float(r["high"] or r["close"] or 0)
        if "low" in r.keys():
            entry["low"] = float(r["low"] or r["close"] or 0)
        if "change_pct" in r.keys():
            entry["change_pct"] = float(r["change_pct"] or 0)
        if "is_suspended" in r.keys():
            entry["is_suspended"] = int(r["is_suspended"] or 0)
        quotes.setdefault(r["code"], {})[r["trade_date"]] = entry

    name_map = {
        r["code"]: r["name"]
        for r in conn.execute("SELECT code, name FROM stocks WHERE is_active=1").fetchall()
    }
    industry_map = {
        r["code"]: (r["industry_sw"] or "")
        for r in conn.execute(
            "SELECT code, industry_sw FROM stocks WHERE is_active=1"
        ).fetchall()
    }

    score_snap: Dict[str, Dict[str, float]] = {}
    dividend_snap: Dict[str, Dict[str, float]] = {}
    factor_factor_id: Optional[str] = None
    strategy_label = strategy
    score_spec = resolve_score_spec(score_strategy)

    if strategy == "factor_combination" or combination_id:
        from services.factor_combinations import get_combination, load_factor_score_snap

        if not combination_id:
            conn.close()
            return {"error": "factor_combination 需 combination_id"}
        combo = get_combination(combination_id)
        if not combo:
            conn.close()
            return {"error": f"合成方案 #{combination_id} 不存在"}
        factor_factor_id = combo.get("output_factor_id")
        if not factor_factor_id:
            conn.close()
            return {"error": "方案尚未 materialize，请先保存并计算合成因子"}
        score_snap = load_factor_score_snap(factor_factor_id, start_str, end_str, conn)
        strategy_label = f"combo#{combination_id}:{factor_factor_id}"
        conn.close()
        if not score_snap:
            return {"error": f"合成因子 {factor_factor_id} 无历史数据"}
    elif strategy.upper().startswith("F") and strategy[1:].isdigit():
        from services.factor_combinations import load_factor_score_snap

        factor_factor_id = strategy.upper()
        score_snap = load_factor_score_snap(factor_factor_id, start_str, end_str, conn)
        strategy_label = factor_factor_id
        conn.close()
        if not score_snap:
            return {"error": f"因子 {factor_factor_id} 无历史数据"}
    elif strategy == "ml_pred":
        from services.ml_gate import is_ml_predictions_approved
        if not is_ml_predictions_approved():
            conn.close()
            return {"error": "ml_pred 需通过 OOS 指标门控（或 AFR_QLIB_PREDICTIONS_APPROVED=true 强制放行）"}
        from config import ML_DEFAULT_HORIZON
        from services.ml_predictions import model_version_for_horizon

        ml_h = ml_horizon if ml_horizon is not None else ML_DEFAULT_HORIZON
        mv = model_version_for_horizon(ml_h)
        conn_ml = sqlite3.connect(DB_PATH)
        conn_ml.row_factory = sqlite3.Row
        for r in conn_ml.execute(
            """SELECT s.code, mp.pred_date, mp.score
               FROM ml_predictions mp JOIN stocks s ON mp.stock_id = s.id
               WHERE s.is_active = 1 AND mp.model_version=?
               ORDER BY mp.pred_date""",
            (mv,),
        ).fetchall():
            score_snap.setdefault(r["code"], {})[r["pred_date"]] = float(r["score"])
        conn_ml.close()
        conn.close()
        if not score_snap:
            return {"error": "无 ML 预测数据，请先运行 qlib train 或 seed-demo"}
    elif strategy == "momentum":
        conn.close()
        strategy_label = "动量因子"
    elif strategy == "turtle":
        conn.close()
        strategy_label = "海龟交易"
    elif strategy == "sector_rotation":
        conn.close()
        strategy_label = "行业轮动"
    elif strategy == "dividend_defensive":
        dividend_snap = load_dividend_yield_snap(conn, start_str, end_str)
        conn.close()
        strategy_label = "红利防御"
        if not dividend_snap:
            return {"error": "红利防御回测无股息率快照，请先同步 valuation_snapshots"}
    elif strategy == "dual_ma":
        conn.close()
        strategy_label = "双均线"
    elif score_spec:
        score_snap = load_score_snap_range(conn, score_spec, start_str, end_str)
        strategy_label = "指数增强" if strategy == "index_enhance" else score_spec.label
        conn.close()
        if not score_snap:
            return {"error": f"策略 {strategy} 无 V5 评分历史"}
    else:
        conn.close()
        return {"error": f"未知策略: {strategy}"}

    use_factor_scores = bool(factor_factor_id) or strategy in (
        "ml_pred",
        "factor_combination",
    ) or bool(score_spec)
    _base_commission = commission_override if commission_override is not None else BACKTEST_COMMISSION
    commission_rate = _base_commission if apply_costs else 0.0
    _slippage = slippage_override if slippage_override is not None else SLIPPAGE
    _stamp_tax = stamp_tax_override if stamp_tax_override is not None else STAMP_TAX
    rebalance_map = {"daily": 1, "weekly": 5, "monthly": 20}
    rebalance_every = rebalance_map.get(rebalance, 5)

    if bench_mode == "csi300":
        benchmark, benchmark_curve = _compute_index_benchmark("sh000300", "沪深300", dates)
        if not benchmark:
            benchmark = _compute_benchmark(quotes, dates)
            benchmark_curve = _compute_benchmark_curve(quotes, dates)
    else:
        benchmark = _compute_benchmark(quotes, dates)
        benchmark_curve = _compute_benchmark_curve(quotes, dates)
    cash = float(initial_cash)
    holdings: Dict[str, dict] = {}
    daily_values: List[dict] = []
    trades: List[dict] = []

    turtle_exit_period = max(10, lookback // 2)

    use_next_open = exec_mode == "next_open"
    for di, dt in enumerate(dates):
        prev_dt = dates[di - 1] if di > 0 else None
        # available: 收盘价，用于信号计算与净值估算
        available = {
            c: quotes[c][dt]["close"]
            for c in quotes
            if dt in quotes[c] and quotes[c][dt]["close"] > 0
        }
        # exec_prices: 实际成交价（next_open 模式用开盘价，fallback close）
        if use_next_open:
            exec_prices = {
                c: (quotes[c][dt].get("open") or quotes[c][dt]["close"])
                for c in available
            }
        else:
            exec_prices = dict(available)
        # prev_close 用于 trade_allowed 内补算 change_pct
        prev_closes: dict[str, float] = {}
        if prev_dt:
            for c in quotes:
                if prev_dt in quotes[c] and quotes[c][prev_dt]["close"] > 0:
                    prev_closes[c] = quotes[c][prev_dt]["close"]

        if strategy == "turtle" and holdings:
            for code in list(holdings.keys()):
                h = holdings[code]
                if apply_t1 and h.get("buy_date") == dt:
                    continue
                if not turtle_should_exit(
                    quotes.get(code, {}),
                    dates,
                    di,
                    exit_period=turtle_exit_period,
                    stop_price=h.get("stop_price"),
                    stop_only=True,
                ):
                    continue
                quote = quotes.get(code, {}).get(dt, {})
                if apply_limit_rules and quote and not trade_allowed("sell", quote, prev_closes.get(code)):
                    continue
                price = exec_prices.get(code, available.get(code, 0))
                if price <= 0:
                    continue
                exec_price = price * (1 - _slippage) if apply_slippage_cost else price
                shares = h["shares"]
                proceeds = shares * exec_price
                if apply_costs:
                    proceeds -= proceeds * (commission_rate + _stamp_tax)
                cash += proceeds
                trades.append(
                    {
                        "date": dt,
                        "code": code,
                        "name": name_map.get(code, ""),
                        "action": "SELL",
                        "shares": shares,
                        "price": round(exec_price, 2),
                        "reason": "turtle_exit",
                    }
                )
                del holdings[code]

        min_di = lookback if strategy in ("momentum", "turtle") else 1
        if strategy == "dividend_defensive":
            min_di = 60
        elif strategy == "dual_ma":
            min_di = 20

        if strategy == "turtle" and di >= min_di:
            day_scores = compute_backtest_day_scores(
                strategy=strategy,
                quotes=quotes,
                dates=dates,
                di=di,
                dt=dt,
                available=available,
                score_snap=score_snap,
                industry_map=industry_map,
                lookback=lookback,
                min_score=min_score,
                sector_window=sector_window,
                use_factor_scores=use_factor_scores,
                dividend_snap=dividend_snap or None,
                top_n=top_n,
            )
            ranked = sorted(day_scores.items(), key=lambda x: -x[1])
            slots = max(0, top_n - len(holdings))
            candidates = [c for c, sc in ranked if sc >= min_score and c not in holdings][:slots]
            if candidates:
                total_investable = cash + sum(
                    holdings.get(c, {}).get("shares", 0) * available.get(c, 0) for c in holdings
                )
                slot_val = total_investable / top_n if top_n else total_investable
                for code in candidates:
                    quote = quotes.get(code, {}).get(dt, {})
                    if apply_limit_rules and quote and not trade_allowed("buy", quote, prev_closes.get(code)):
                        continue
                    price = exec_prices.get(code, available.get(code, 0))
                    if price <= 0:
                        continue
                    exec_price = price * (1 + _slippage) if apply_slippage_cost else price
                    diff = max(0, int(slot_val / exec_price / 100) * 100)
                    if diff <= 0:
                        continue
                    cost = diff * exec_price
                    if apply_costs:
                        cost += cost * commission_rate
                    if cost > cash:
                        diff = max(
                            0,
                            int(cash / (exec_price * (1 + commission_rate if apply_costs else 0)) / 100)
                            * 100,
                        )
                        cost = diff * exec_price * (1 + commission_rate if apply_costs else 1)
                    if diff > 0 and cost <= cash:
                        cash -= cost
                        atr = turtle_atr(quotes.get(code, {}), dates, di, period=lookback)
                        holdings[code] = {
                            "shares": diff,
                            "cost": exec_price,
                            "buy_date": dt,
                            "entry_price": exec_price,
                            "stop_price": round(exec_price - 2 * atr, 4) if atr and atr > 0 else None,
                        }
                        trades.append(
                            {
                                "date": dt,
                                "code": code,
                                "name": name_map.get(code, ""),
                                "action": "BUY",
                                "shares": diff,
                                "price": round(exec_price, 2),
                                "reason": "turtle_entry",
                            }
                        )
        elif di % rebalance_every == 0 and di >= min_di:
            day_scores = compute_backtest_day_scores(
                strategy=strategy,
                quotes=quotes,
                dates=dates,
                di=di,
                dt=dt,
                available=available,
                score_snap=score_snap,
                industry_map=industry_map,
                lookback=lookback,
                min_score=min_score,
                sector_window=sector_window,
                use_factor_scores=use_factor_scores,
                dividend_snap=dividend_snap or None,
                top_n=top_n,
            )

            ranked = sorted(day_scores.items(), key=lambda x: -x[1])
            selected = [c for c, _ in ranked[:top_n]]

            for code in list(holdings.keys()):
                if code not in selected:
                    if apply_t1 and holdings[code].get("buy_date") == dt:
                        continue
                    quote = quotes.get(code, {}).get(dt, {})
                    if apply_limit_rules and quote and not trade_allowed("sell", quote, prev_closes.get(code)):
                        continue
                    price = exec_prices.get(code, available.get(code, 0))
                    if price <= 0:
                        continue
                    exec_price = price * (1 - _slippage) if apply_slippage_cost else price
                    shares = holdings[code]["shares"]
                    proceeds = shares * exec_price
                    if apply_costs:
                        proceeds -= proceeds * (commission_rate + _stamp_tax)
                    cash += proceeds
                    trades.append(
                        {
                            "date": dt,
                            "code": code,
                            "name": name_map.get(code, ""),
                            "action": "SELL",
                            "shares": shares,
                            "price": round(exec_price, 2),
                        }
                    )
                    del holdings[code]

            if selected:
                total_investable = cash + sum(
                    holdings.get(c, {}).get("shares", 0) * available.get(c, 0) for c in holdings
                )
                if pos_style == "weighted":
                    total_w = sum(day_scores[c] for c in selected)
                    weights = {
                        c: day_scores[c] / total_w if total_w else 1 / len(selected) for c in selected
                    }
                else:
                    weights = {c: 1 / len(selected) for c in selected}

                for code in selected:
                    quote = quotes.get(code, {}).get(dt, {})
                    if apply_limit_rules and quote and not trade_allowed("buy", quote, prev_closes.get(code)):
                        continue
                    price = exec_prices.get(code, available.get(code, 0))
                    if price <= 0:
                        continue
                    exec_price = price * (1 + _slippage) if apply_slippage_cost else price
                    target_val = total_investable * weights[code]
                    target_shares = max(0, int(target_val / exec_price / 100) * 100)
                    current = holdings.get(code, {}).get("shares", 0)
                    diff = target_shares - current
                    if diff > 0:
                        cost = diff * exec_price
                        if apply_costs:
                            cost += cost * commission_rate
                        if cost > cash:
                            diff = max(
                                0,
                                int(cash / (exec_price * (1 + commission_rate if apply_costs else 0)) / 100)
                                * 100,
                            )
                            cost = diff * exec_price * (1 + commission_rate if apply_costs else 1)
                        if diff > 0 and cost <= cash:
                            cash -= cost
                            if code in holdings:
                                old = holdings[code]
                                total_sh = old["shares"] + diff
                                avg = (old["shares"] * old["cost"] + diff * exec_price) / total_sh
                                pos = {
                                    "shares": total_sh,
                                    "cost": avg,
                                    "buy_date": old.get("buy_date"),
                                }
                                if strategy == "turtle":
                                    pos["stop_price"] = old.get("stop_price")
                                    pos["entry_price"] = old.get("entry_price", avg)
                                holdings[code] = pos
                            else:
                                pos = {"shares": diff, "cost": exec_price, "buy_date": dt}
                                if strategy == "turtle":
                                    atr = turtle_atr(quotes.get(code, {}), dates, di, period=lookback)
                                    pos["entry_price"] = exec_price
                                    pos["stop_price"] = (
                                        round(exec_price - 2 * atr, 4) if atr and atr > 0 else None
                                    )
                                holdings[code] = pos
                            trades.append(
                                {
                                    "date": dt,
                                    "code": code,
                                    "name": name_map.get(code, ""),
                                    "action": "BUY",
                                    "shares": diff,
                                    "price": round(exec_price, 2),
                                }
                            )
                    elif diff < 0:
                        if apply_t1 and holdings[code].get("buy_date") == dt:
                            continue
                        if apply_limit_rules and quote and not trade_allowed("sell", quote, prev_closes.get(code)):
                            continue
                        sell_sh = -diff
                        proceeds = sell_sh * exec_price
                        if apply_costs:
                            proceeds -= proceeds * (commission_rate + _stamp_tax)
                        cash += proceeds
                        holdings[code]["shares"] -= sell_sh
                        if holdings[code]["shares"] <= 0:
                            del holdings[code]
                        trades.append(
                            {
                                "date": dt,
                                "code": code,
                                "name": name_map.get(code, ""),
                                "action": "SELL",
                                "shares": sell_sh,
                                "price": round(exec_price, 2),
                            }
                        )

        hold_val = sum(holdings.get(c, {}).get("shares", 0) * available.get(c, 0) for c in holdings)
        daily_values.append(
            {"date": dt, "value": round(cash + hold_val, 2), "cash": round(cash, 2), "holdings": len(holdings)}
        )

    current_holdings = _build_holdings(holdings, available, name_map)
    result = _calc_metrics(
        daily_values,
        dates,
        trades,
        days,
        top_n,
        lookback,
        pos_style,
        rebalance_every,
        benchmark,
        current_holdings,
        strategy_label,
        apply_costs,
        apply_t1,
        benchmark_curve,
        combination_id=combination_id,
        commission_rate=commission_rate,
        benchmark_mode=bench_mode,
        exec_mode=exec_mode,
        initial_cash=initial_cash,
        risk_free_rate=risk_free_rate,
    )
    from services.beta_health import attach_meta

    return attach_meta(result)


def run_parameter_scan(
    days: int = 90,
    top_n_list: Optional[List[int]] = None,
    min_score_list: Optional[List[float]] = None,
    strategy: str = "composite",
) -> dict:
    """简单参数网格搜索。"""
    top_n_list = top_n_list or [3, 5, 8]
    min_score_list = min_score_list or [45, 50, 55, 60]
    results = []
    for tn in top_n_list:
        for ms in min_score_list:
            r = run_backtest(days=days, top_n=tn, min_score=ms, strategy=strategy)
            if "error" in r:
                continue
            results.append(
                {
                    "top_n": tn,
                    "min_score": ms,
                    "total_return_pct": r["total_return_pct"],
                    "sharpe": r["sharpe"],
                    "max_drawdown_pct": r["max_drawdown_pct"],
                }
            )
    results.sort(key=lambda x: -x["sharpe"])
    from services.beta_health import attach_meta

    return attach_meta({"strategy": strategy, "days": days, "results": results[:20], "best": results[0] if results else None})


def run_rolling_backtest(
    window: int = 60,
    step: int = 20,
    top_n: int = 5,
    min_score: float = 50,
    strategy: str = "composite",
    stop_loss: float = -0.10,
    take_profit: float = 0.30,
) -> dict:
    """滚动窗口回测 — 统一引擎"""
    t0 = time.time()
    window_results = []
    win = 0
    while True:
        days = window + step * win + step
        r = run_backtest(
            days=min(days, window + step * (win + 2)),
            top_n=top_n,
            min_score=min_score,
            strategy=strategy,
            rebalance="weekly",
        )
        if "error" in r:
            break
        dv = r.get("daily_values") or []
        if len(dv) < window:
            break
        seg = dv[-window - step :] if len(dv) >= window + step else dv
        if len(seg) < 2:
            break
        start_v, end_v = seg[0]["value"], seg[-1]["value"]
        ret = (end_v / start_v - 1) * 100 if start_v else 0
        window_results.append(
            {
                "period": f"{seg[0]['date']}~{seg[-1]['date']}",
                "return_pct": round(ret, 2),
                "trades": r.get("trade_count", 0),
                "sharpe": r.get("sharpe", 0),
            }
        )
        win += 1
        if win >= 8:
            break

    if not window_results:
        return {"error": "数据不足，无法滚动回测"}

    returns = [w["return_pct"] for w in window_results]
    win_rate = sum(1 for x in returns if x > 0) / len(returns) * 100
    avg_ret = sum(returns) / len(returns)
    std_ret = math.sqrt(sum((x - avg_ret) ** 2 for x in returns) / max(len(returns) - 1, 1))
    peak, max_dd, cum = 100.0, 0.0, 100.0
    for ret in returns:
        cum *= 1 + ret / 100
        peak = max(peak, cum)
        max_dd = min(max_dd, (cum - peak) / peak * 100)

    from services.beta_health import attach_meta

    return attach_meta(
        {
            "params": {
                "window": window,
                "step": step,
                "top_n": top_n,
                "min_score": min_score,
                "strategy": strategy,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "engine": "unified",
            },
            "windows": len(window_results),
            "win_rate": round(win_rate, 1),
            "avg_return": round(avg_ret, 2),
            "sharpe": round(avg_ret / std_ret * math.sqrt(252 / max(step, 1)), 2) if std_ret else 0,
            "max_drawdown": round(max_dd, 2),
            "window_results": window_results,
            "duration_ms": round((time.time() - t0) * 1000),
        }
    )


def run_walk_forward(
    train_days: int = 60,
    test_days: int = 20,
    top_n: int = 5,
    strategy: str = "composite",
) -> dict:
    """Walk-forward：训练窗选参 + 测试窗验证"""
    scans = run_parameter_scan(days=train_days, strategy=strategy)
    best = scans.get("best")
    if not best:
        return {"error": "Walk-forward 训练窗参数扫描无结果"}
    test = run_backtest(
        days=train_days + test_days,
        top_n=best["top_n"],
        min_score=best["min_score"],
        strategy=strategy,
    )
    if "error" in test:
        return test
    from services.beta_health import attach_meta

    return attach_meta(
        {
            "train_days": train_days,
            "test_days": test_days,
            "best_params": best,
            "test_return_pct": test.get("total_return_pct"),
            "test_sharpe": test.get("sharpe"),
            "test_max_drawdown_pct": test.get("max_drawdown_pct"),
            "in_sample_scan": scans.get("results", [])[:5],
        }
    )


def _ensure_backtest_runs_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            params_json TEXT NOT NULL,
            result_json TEXT NOT NULL
        )
    """)


def save_backtest_run(params: dict, result: dict) -> dict:
    conn = sqlite3.connect(DB_PATH)
    _ensure_backtest_runs_table(conn)
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO backtest_runs (created_at, params_json, result_json) VALUES (?,?,?)",
        (now, json.dumps(params, ensure_ascii=False), json.dumps(result, ensure_ascii=False)),
    )
    rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return {"id": rid, "created_at": now}


def list_backtest_runs(limit: int = 10) -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    _ensure_backtest_runs_table(conn)
    rows = conn.execute(
        "SELECT id, created_at, params_json, result_json FROM backtest_runs ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    runs = []
    for r in rows:
        params = json.loads(r["params_json"])
        res = json.loads(r["result_json"])
        runs.append(
            {
                "id": r["id"],
                "created_at": r["created_at"],
                "params": params,
                "total_return_pct": res.get("total_return_pct"),
                "sharpe": res.get("sharpe"),
                "strategy": params.get("strategy"),
            }
        )
    return {"runs": runs}


def _compute_benchmark_curve(quotes: dict, dates: List[str]) -> List[dict]:
    """等权买入持有基准每日净值"""
    if len(dates) < 2:
        return []
    valid = [c for c in quotes if dates[0] in quotes[c]]
    if len(valid) < 3:
        return []
    per = 100_000 / len(valid)
    shares = {c: per / quotes[c][dates[0]]["close"] for c in valid if quotes[c][dates[0]]["close"] > 0}
    curve = []
    for dt in dates:
        val = sum(shares[c] * quotes[c][dt]["close"] for c in shares if dt in quotes[c])
        curve.append({"date": dt, "value": round(val, 2)})
    return curve


def _compute_index_benchmark(
    index_code: str, index_name: str, dates: List[str]
) -> tuple[Optional[dict], List[dict]]:
    """沪深300 等指数买入持有基准。"""
    if len(dates) < 2:
        return None, []
    try:
        from services.market_index import fetch_index_kline

        data = fetch_index_kline(index_code, days=max(len(dates) + 30, 120))
        kline = {str(b["date"]): float(b["close"]) for b in (data.get("kline") or []) if b.get("close")}
    except Exception:
        return None, []

    aligned = [(d, kline[d]) for d in dates if d in kline and kline[d] > 0]
    if len(aligned) < 2:
        return None, []

    start_price = aligned[0][1]
    end_price = aligned[-1][1]
    curve = [
        {"date": d, "value": round(100_000 * px / start_price, 2)}
        for d, px in aligned
    ]
    return (
        {
            "name": index_name,
            "code": index_code,
            "return_pct": round((end_price / start_price - 1) * 100, 2),
        },
        curve,
    )


def _compute_benchmark(quotes: dict, dates: List[str]) -> Optional[dict]:
    if len(dates) < 2:
        return None
    valid = [c for c in quotes if dates[0] in quotes[c] and dates[-1] in quotes[c]]
    if len(valid) < 3:
        return None
    per = 100_000 / len(valid)
    end_val = 0.0
    for code in valid:
        sp = quotes[code][dates[0]]["close"]
        ep = quotes[code][dates[-1]]["close"]
        if sp > 0:
            end_val += per * ep / sp
    return {
        "name": f"等权基准({len(valid)}只)",
        "return_pct": round((end_val / 100_000 - 1) * 100, 2),
    }


def _build_holdings(holdings: dict, available: dict, name_map: dict) -> List[dict]:
    raw = []
    total_hv = 0.0
    for code, h in holdings.items():
        price = available.get(code, h["cost"])
        mv = h["shares"] * price
        total_hv += mv
        raw.append(
            {
                "code": code,
                "name": name_map.get(code, ""),
                "shares": h["shares"],
                "price": round(price, 2),
                "value": round(mv, 2),
                "pnl_pct": round((price / h["cost"] - 1) * 100, 1) if h["cost"] else 0,
            }
        )
    for h in raw:
        h["weight_pct"] = round(h["value"] / total_hv * 100, 1) if total_hv > 0 else 0
    return raw


def _calc_metrics(
    daily_records,
    dates,
    trades,
    days,
    top_n,
    lookback,
    pos_style,
    rebalance_every,
    benchmark,
    current_holdings,
    strategy_label,
    apply_costs,
    apply_t1,
    benchmark_curve=None,
    combination_id: Optional[int] = None,
    commission_rate: float = BACKTEST_COMMISSION,
    benchmark_mode: str = "equal",
    exec_mode: str = "next_open",
    initial_cash: float = 100_000.0,
    risk_free_rate: float = 0.02,
):
    final_val = daily_records[-1]["value"] if daily_records else initial_cash
    returns = [
        (daily_records[i]["value"] / daily_records[i - 1]["value"] - 1)
        for i in range(1, len(daily_records))
    ]
    rf_daily = (1 + risk_free_rate) ** (1 / 252) - 1
    avg_ret = sum(returns) / len(returns) if returns else 0
    std_ret = math.sqrt(sum((x - avg_ret) ** 2 for x in returns) / len(returns)) if returns else 0
    # Sharpe：超额收益 / 波动率（减 rf）
    if std_ret > 0 and returns:
        excess_daily = [r - rf_daily for r in returns]
        avg_excess = sum(excess_daily) / len(excess_daily)
        sharpe = avg_excess / std_ret * math.sqrt(252)
    else:
        sharpe = 0.0
    # Information Ratio：相对 benchmark 日超额 / 跟踪误差
    information_ratio = None
    if benchmark_curve and len(benchmark_curve) >= 2:
        bm_vals = {r["date"]: r["value"] for r in benchmark_curve}
        bm_rets = []
        for i in range(1, len(daily_records)):
            d_cur = daily_records[i]["date"]
            d_pre = daily_records[i - 1]["date"]
            if d_cur in bm_vals and d_pre in bm_vals and bm_vals[d_pre] > 0:
                bm_rets.append(bm_vals[d_cur] / bm_vals[d_pre] - 1)
        if len(bm_rets) == len(returns) and len(returns) > 1:
            active = [r - b for r, b in zip(returns, bm_rets)]
            avg_a = sum(active) / len(active)
            std_a = math.sqrt(sum((x - avg_a) ** 2 for x in active) / len(active))
            information_ratio = round(avg_a / std_a * math.sqrt(252), 2) if std_a > 0 else None

    peak = daily_records[0]["value"] if daily_records else initial_cash
    peak_date = daily_records[0]["date"] if daily_records else ""
    max_dd, max_dd_s, max_dd_e = 0, "", ""
    drawdowns = []
    for r in daily_records:
        v = r["value"]
        if v > peak:
            peak = v
            peak_date = r["date"]
        dd = (peak - v) / peak if peak > 0 else 0
        drawdowns.append({"date": r["date"], "drawdown_pct": round(dd * 100, 2)})
        if dd > max_dd:
            max_dd, max_dd_s, max_dd_e = dd, peak_date, r["date"]

    td = max(len(returns), 1)
    ann_ret = ((final_val / initial_cash) ** (252 / td) - 1) * 100
    ann_vol = std_ret * math.sqrt(252) * 100 if std_ret else 0
    calmar = ann_ret / (max_dd * 100) if max_dd > 0 else 0
    win_rate = sum(1 for r in returns if r > 0) / len(returns) * 100 if returns else 0

    monthly = {}
    for i, r in enumerate(returns):
        m = daily_records[i + 1]["date"][:7]
        monthly[m] = monthly.get(m, 1) * (1 + r)

    excess = None
    if benchmark:
        excess = round((final_val / initial_cash - 1) * 100 - benchmark["return_pct"], 2)

    # T+1 模型：dates[0] 无成交（warmup），有效交易日少 1
    warmup_days = 1 if exec_mode == "next_open" else 0
    effective_trading_days = max(len(dates) - warmup_days, 1)

    return {
        "params": {
            "days": days,
            "top_n": top_n,
            "lookback": lookback,
            "pos_style": pos_style,
            "strategy": strategy_label,
            "combination_id": combination_id,
            "rebalance": f"每{rebalance_every}天",
            "costs": apply_costs,
            "commission": commission_rate,
            "slippage": SLIPPAGE,
            "t1": apply_t1,
            "engine": "unified",
            "benchmark_mode": benchmark_mode,
            "exec_model": exec_mode,
            "warmup_days": warmup_days,
            "effective_trading_days": effective_trading_days,
            "monthly_method": "compound_daily_in_month",
        },
        "start_value": initial_cash,
        "final_value": round(final_val, 2),
        "total_return_pct": round((final_val / initial_cash - 1) * 100, 2),
        "annualized_return_pct": round(ann_ret, 2),
        "annualized_vol_pct": round(ann_vol, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "max_drawdown_period": f"{max_dd_s} ~ {max_dd_e}",
        "sharpe": round(sharpe, 2),
        "information_ratio": information_ratio,
        "risk_free_rate": risk_free_rate,
        "calmar": round(calmar, 2),
        "win_rate_pct": round(win_rate, 1),
        "trade_count": len(trades),
        "daily_values": daily_records,
        "drawdowns": drawdowns,
        "monthly_returns": {m: round((v - 1) * 100, 2) for m, v in monthly.items()},
        "recent_trades": trades[-20:],
        "current_holdings": current_holdings,
        "benchmark": benchmark,
        "benchmark_curve": benchmark_curve or [],
        "excess_return_pct": excess,
        "factor_contrib": {"策略": strategy_label},
    }
