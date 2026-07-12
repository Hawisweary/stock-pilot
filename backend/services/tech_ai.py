"""技术面 AI 多周期深度分析服务（带缓存）"""
import json
import sqlite3
import hashlib
from services.llm_client import chat_completion, is_llm_available

from config import DB_PATH


def _hash_input(
    daily: dict,
    weekly: dict,
    market_snapshot: dict | None = None,
) -> str:
    """基于个股 + 大盘最新指标生成缓存哈希"""
    from services.market_index import market_hash_part

    raw = (
        f"{daily.get('close','')}|{daily.get('rsi14','')}|{daily.get('macd_dif','')}|{daily.get('kdj_k','')}"
        f"|{weekly.get('close','')}|{weekly.get('rsi14','')}|{weekly.get('macd_dif','')}"
        f"|{market_hash_part(market_snapshot)}"
    )
    return hashlib.md5(raw.encode()).hexdigest()


SYSTEM_PROMPT = """你是一位 A 股技术分析专家。根据提供的个股日线/周线指标，并结合大盘指数（上证、沪深300、创业板等）环境，进行多周期综合分析。

个股评分须考虑 Beta 环境：大盘共振偏多时可略提高个股分，大盘偏空时个股独立强势才可给高分，弱势股应更低。
请严格按照 JSON 格式输出，不要包含任何额外说明文字：

{
  "market": {
    "environment": "偏多/偏空/震荡",
    "comment": "一句话大盘研判"
  },
  "daily": {
    "signal": "偏多/偏空/震荡",
    "strength": 0-100,
    "key_signals": ["金叉", "超卖"],
    "resistance": 压力位价格,
    "support": 支撑位价格
  },
  "weekly": {
    "signal": "偏多/偏空/震荡",
    "strength": 0-100,
    "key_signals": [],
    "resistance": null,
    "support": null
  },
  "confluence": "共振偏多/共振偏空/背离/一致中性",
  "score": 0-100,
  "advice": "一句话操作建议",
  "risk_level": "低/中/高",
  "reasoning": "2-3句判断依据"
}

信号强度 (strength): 70+为强, 40-70为中, 40以下为弱
共振判断: 日线和周线同向为共振, 反向为背离, 同向中性为一致中性
评分 (score): 综合个股日线/周线与大盘环境, 0-30空, 30-50中性偏空, 50-70中性偏多, 70-100多
"""


def build_tech_prompt(
    daily_indicators: dict,
    weekly_indicators: dict,
    stock_name: str,
    market_snapshot: dict | None = None,
) -> str:
    """构建技术面分析 prompt"""
    def fmt(v):
        if v is None: return "N/A"
        return f"{v:.2f}" if isinstance(v, float) else str(v)

    from services.market_index import format_market_index_text

    d = daily_indicators
    w = weekly_indicators
    market_block = format_market_index_text(market_snapshot)

    return f"""分析股票 {stock_name} 的技术面状态（须结合大盘环境）：

=== 大盘指数 ===
{market_block}

=== 个股日线指标 ===
收盘价: {fmt(d.get('close'))} | 成交量: {fmt(d.get('volume'))}
MA5: {fmt(d.get('ma5'))} | MA10: {fmt(d.get('ma10'))} | MA20: {fmt(d.get('ma20'))}
MACD: DIF={fmt(d.get('macd_dif'))} DEA={fmt(d.get('macd_dea'))} BAR={fmt(d.get('macd_bar'))}
KDJ: K={fmt(d.get('kdj_k'))} D={fmt(d.get('kdj_d'))} J={fmt(d.get('kdj_j'))}
RSI(14): {fmt(d.get('rsi14'))}
BOLL: 上轨={fmt(d.get('boll_upper'))} 中轨={fmt(d.get('boll_mid'))} 下轨={fmt(d.get('boll_lower'))}
ATR(14): {fmt(d.get('atr14'))}

=== 个股周线指标 ===
收盘价: {fmt(w.get('close'))}
MA5: {fmt(w.get('ma5'))} | MA10: {fmt(w.get('ma10'))} | MA20: {fmt(w.get('ma20'))}
MACD: DIF={fmt(w.get('macd_dif'))} DEA={fmt(w.get('macd_dea'))} BAR={fmt(w.get('macd_bar'))}
RSI(14): {fmt(w.get('rsi14'))}
BOLL: 上轨={fmt(w.get('boll_upper'))} 中轨={fmt(w.get('boll_mid'))} 下轨={fmt(w.get('boll_lower'))}

请综合大盘与个股日线/周线，给出技术面评分与操作建议。"""


def analyze_technical(
    daily: dict,
    weekly: dict,
    stock_name: str,
    stock_id: int = 0,
    *,
    market_snapshot: dict | None = None,
) -> dict:
    """带缓存的 AI 技术面分析（可选大盘指数环境）"""
    if market_snapshot is None:
        try:
            from services.market_index import fetch_market_index_snapshot

            market_snapshot = fetch_market_index_snapshot()
        except Exception:
            market_snapshot = None

    input_hash = _hash_input(daily, weekly, market_snapshot)

    # 查缓存
    if stock_id > 0:
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT full_result, created_at FROM tech_analysis_cache WHERE stock_id=? AND input_hash=?",
                (stock_id, input_hash)
            ).fetchone()
            conn.close()
            if row:
                result = json.loads(row["full_result"])
                result["cached"] = True
                result["cached_at"] = row["created_at"]
                result = _apply_market_multiplier(result); return result
        except Exception:
            pass

    if not is_llm_available():
        # 规则引擎回退：基于技术指标自动生成分析
        return rule_based_analysis(daily, weekly, stock_name, market_snapshot=market_snapshot)
    prompt = build_tech_prompt(daily, weekly, stock_name, market_snapshot)
    try:
        text = chat_completion(
            prompt,
            system_prompt=SYSTEM_PROMPT,
            max_tokens=800,
            temperature=0.2,
        )
        start = text.find("{")
        end = text.rfind("}")
        result = {}
        if start >= 0 and end > start:
            result = json.loads(text[start:end+1])
        else:
            return {"error": "解析失败", "raw": text[:200]}

        # 写入缓存
        if stock_id > 0:
            try:
                conn = sqlite3.connect(DB_PATH)
                conn.execute(
                    """INSERT OR REPLACE INTO tech_analysis_cache
                       (stock_id, input_hash, daily_close, weekly_close, score, signal, advice, reasoning, full_result)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (stock_id, input_hash,
                     daily.get("close"), weekly.get("close"),
                     result.get("score"), result.get("advice",""),
                     result.get("confluence",""), result.get("reasoning",""),
                     json.dumps(result, ensure_ascii=False))
                )
                conn.commit()
                conn.close()
            except Exception:
                pass

        result = _apply_market_multiplier(result); return result
    except Exception as e:
        return {"error": str(e)[:100]}




def _calc_adx(closes, period=14):
    """计算 ADX 指标"""
    if len(closes) < period+1: return 20
    highs = closes; lows = closes  # 简化：仅用收盘价
    tr_list, plus_dm, minus_dm = [], [], []
    for i in range(1, len(closes)):
        h, l, pc = closes[i], closes[i], closes[i-1]
        tr = max(h-l, abs(h-pc), abs(l-pc))
        tr_list.append(tr)
        plus_dm.append(max(h-closes[i-1], 0) if h-closes[i-1] > closes[i-1]-l else 0)
        minus_dm.append(max(closes[i-1]-l, 0) if closes[i-1]-l > h-closes[i-1] else 0)
    def _wildersmooth(data, n):
        result = [sum(data[:n])]
        for i in range(n, len(data)): result.append(result[-1] - result[-1]/n + data[i])
        result = _apply_market_multiplier(result); return result
    atr = _wildersmooth(tr_list, period)
    pdi = [100 * sum(plus_dm[:period]) / atr[0]]; ndi = [100 * sum(minus_dm[:period]) / atr[0]]
    for i in range(1, len(atr)):
        pdi.append(100 * (pdi[-1] * (period-1)/period + plus_dm[period+i-1]*100/atr[i]) if atr[i] > 0 else pdi[-1])
        ndi.append(100 * (ndi[-1] * (period-1)/period + minus_dm[period+i-1]*100/atr[i]) if atr[i] > 0 else ndi[-1])
    dx = [100 * abs(p-n)/max(p+n, 0.01) for p, n in zip(pdi, ndi)]
    adx_val = sum(dx[-period:]) / period if len(dx) >= period else 20
    return round(adx_val, 1)

def _calc_macd_bar(offset, closes, ema12, ema26):
    """计算指定偏移的MACD柱"""
    idx = offset if offset < 0 else offset - len(closes)
    dif = ema12[idx] - ema26[idx]
    dea = sum(ema12[j]-ema26[j] for j in range(idx-9, idx)) / 9 if idx >= 9 else dif
    return 2*(dif - dea)

def _calc_rsi(closes, period=14):
    """计算RSI"""
    if len(closes) < period+1: return 50
    rets = [closes[i]/closes[i-1]-1 for i in range(1, len(closes))]
    gains = sum(r for r in rets if r > 0); losses = abs(sum(r for r in rets if r < 0))
    rs = gains/(losses+0.001)
    return 100 - 100/(1+rs)


def _apply_market_multiplier(result: dict) -> dict:
    """V2: LLM市场研判作为调节系数（±0.1）"""
    confluence = result.get("confluence", "")
    mult = 1.0
    if "强烈看多" in confluence or "极度看多" in confluence: mult = 1.10
    elif "看多" in confluence or "偏多" in confluence: mult = 1.05
    elif "看空" in confluence or "偏空" in confluence: mult = 0.95
    elif "强烈看空" in confluence or "极度看空" in confluence: mult = 0.90
    if mult != 1.0 and "score" in result:
        result["raw_score"] = result["score"]
        result["score"] = round(result["score"] * mult, 1)
        result["market_multiplier"] = round(mult, 3)
    return result

def rule_based_analysis(
    daily,
    weekly,
    stock_name: str,
    *,
    market_snapshot: dict | None = None,
) -> dict:
    """无LLM时的规则引擎技术分析"""
    import math

    # 接受 compute_technical_indicators 输出的 dict（包含 closes 价格序列）或 DataFrame
    if isinstance(daily, dict):
        closes_d = daily.get('closes', [])
    elif hasattr(daily, 'columns'):
        closes_d = list(daily['close'].values[-60:])
    elif isinstance(daily, list) and daily and isinstance(daily[0], dict):
        closes_d = [d["close"] for d in daily[-60:]]
    else:
        closes_d = list(daily)[-60:] if hasattr(daily, '__iter__') else []
    
    if len(closes_d) < 20:
        return {"error": "数据不足", "score": 50, "advice": "需要至少20个日线数据"}

    # MACD
    def ema(data, n):
        k = 2/(n+1); result = [data[0]]
        for x in data[1:]: result.append(x*k + result[-1]*(1-k))
        result = _apply_market_multiplier(result); return result
    ema12 = ema(closes_d, 12); ema26 = ema(closes_d, 26)
    dif = ema12[-1] - ema26[-1]
    dea_val = sum([ema12[i]-ema26[i] for i in range(-9,0)])/9 if len(closes_d)>=9 else dif
    macd_bar = 2*(dif - dea_val)

    # RSI(14)
    rets = [closes_d[i]/closes_d[i-1]-1 for i in range(1, min(15, len(closes_d)))]
    gains = sum(r for r in rets if r > 0); losses = abs(sum(r for r in rets if r < 0))
    rs = gains/(losses+0.001); rsi = 100 - 100/(1+rs)

    # MA判断
    ma5 = sum(closes_d[-5:])/5; ma20 = sum(closes_d[-20:])/20
    trend = "上涨" if ma5 > ma20 else "下跌"
    change_5d = (closes_d[-1]/closes_d[-6]-1)*100 if len(closes_d)>=6 else 0
    change_20d = (closes_d[-1]/closes_d[-21]-1)*100 if len(closes_d)>=21 else 0
    vol = math.sqrt(sum(r**2 for r in rets)/len(rets))*100 if rets else 5

    # V2: ADX 状态识别
    adx = _calc_adx(closes_d) if len(closes_d) >= 20 else 20
    is_trending = adx > 25

    # 评分（V2优化：ADX自适应权重）
    score = 50
    signals = []
    # MACD 信号滤波：柱状线连续2日同向才有效
    macd_valid = True
    if len(closes_d) >= 3:
        bars = [_calc_macd_bar(i, closes_d, ema12, ema26) for i in range(-3, 0)]
        macd_valid = (bars[-1] > 0 and bars[-2] > 0) or (bars[-1] < 0 and bars[-2] < 0)
    macd_w = 12 if macd_valid else 0
    if is_trending: macd_w = int(macd_w * 1.5); signals.append(f"ADX={adx:.0f}趋势市")
    else: macd_w = int(macd_w * 0.5); signals.append(f"ADX={adx:.0f}震荡市")
    if macd_bar > 0 and macd_w > 0: score += macd_w; signals.append("MACD金叉📈")
    elif macd_bar < 0 and macd_w > 0: score -= macd_w; signals.append("MACD死叉📉")
    if macd_w == 0: signals.append("MACD信号过滤")
    # RSI 自适应（V2：背离优先检测）
    div_detected = False
    if len(closes_d) >= 20:
        # 简化的RSI背离：价格新低但RSI未新低
        rsi_vals = [_calc_rsi(closes_d[max(0,i-14):i+1]) for i in range(len(closes_d)-15, len(closes_d))]
        if len(rsi_vals) >= 10:
            price_p1 = closes_d[-1]; price_p10 = min(closes_d[-11:])
            rsi_cur = rsi_vals[-1]; rsi_min = min(rsi_vals)
            if abs(price_p1 - price_p10) / max(abs(price_p10),0.01) > 0.03 and rsi_cur > rsi_min * 1.05:
                score += 6; signals.append("RSI底背离📈"); div_detected = True
    if not div_detected:
        rsi_w = 15 if not is_trending else 8
        if rsi > 70: score -= rsi_w; signals.append(f"RSI过热({rsi:.0f})")
        elif rsi < 30: score += rsi_w; signals.append(f"RSI超卖({rsi:.0f})")
        else: signals.append(f"RSI中性({rsi:.0f})")
    # V2: BOLL带量突破验证
    if len(closes_d) >= 20:
        sma20 = sum(closes_d[-20:])/20; std20 = math.sqrt(sum((c-sma20)**2 for c in closes_d[-20:])/20)
        upper = sma20 + 2*std20; lower = sma20 - 2*std20
        vols = [closes_d[i] for i in range(len(closes_d)-5, len(closes_d))]
        prev_vol_avg = sum(closes_d[-5:-1])/4 if len(closes_d)>=5 else 1
        if closes_d[-1] > upper and closes_d[-2] <= upper:
            if prev_vol_avg > 0 and closes_d[-1] > prev_vol_avg * 1.3:
                score += 8; signals.append("BOLL带量突破📈")
            else: signals.append("BOLL假突破(无放量)")
        elif closes_d[-1] < lower and closes_d[-2] >= lower:
            score -= 6; signals.append("BOLL下突破📉")
    # 均线趋势
    if trend == "上涨": score += 10; signals.append("均线多头排列")
    else: score -= 5; signals.append("均线空头排列")
    if change_20d > 5: score += 5
    elif change_20d < -5: score -= 5
    score = max(0, min(100, score))

    market_note = ""
    if market_snapshot:
        sh = market_snapshot.get("上证指数", {}).get("daily") or {}
        ch5 = sh.get("change_5d_pct")
        rsi_m = sh.get("rsi14")
        if ch5 is not None and ch5 < -3:
            score = max(0, score - 4)
            market_note = "大盘5日走弱"
        elif ch5 is not None and ch5 > 3:
            score = min(100, score + 3)
            market_note = "大盘5日偏强"
        elif rsi_m is not None and rsi_m < 35:
            score = max(0, score - 2)
            market_note = "大盘RSI超卖环境"
        elif rsi_m is not None and rsi_m > 65:
            score = min(100, score + 2)
            market_note = "大盘RSI偏强环境"
        score = max(0, min(100, score))

    advice = f"{stock_name} 当前{trend}趋势，5日涨跌{change_5d:+.1f}%，20日涨跌{change_20d:+.1f}%。"
    advice += f" 波动率{vol:.1f}%。"
    if market_note:
        advice += f" 大盘:{market_note}。"

    return {
        "score": round(score, 1),
        "advice": advice,
        "signals": signals,
        "indicators": {
            "trend": trend, "rsi": round(rsi, 1), "macd_bar": round(macd_bar, 3),
            "change_5d": round(change_5d, 1), "change_20d": round(change_20d, 1),
            "volatility": round(vol, 1), "ma5": round(ma5, 2), "ma20": round(ma20, 2),
        },
        "engine": "规则引擎（LLM未配置）"
    }

def compute_technical_indicators(closes, highs, lows, volumes=None):
    """复用已有指标计算，返回最新一期摘要"""
    from services.MyTT import MACD, KDJ, RSI, BOLL, ATR, MA
    import numpy as np

    c = np.array(closes, dtype=float)
    h = np.array(highs, dtype=float)
    l = np.array(lows, dtype=float)

    macd_dif, macd_dea, macd_bar = MACD(c)
    k, d, j = KDJ(c, h, l)
    rsi = RSI(c, 14)
    upper, mid, lower = BOLL(c)
    atr = ATR(c, h, l, 14)
    ma5 = MA(c, 5)
    ma10 = MA(c, 10)
    ma20 = MA(c, 20)

    last = -1
    return {
        "close": _s0(c[last]),
        "volume": _s0(volumes[last]) if volumes is not None and len(volumes) > 0 else None,
        "ma5": _s0(ma5[last]), "ma10": _s0(ma10[last]), "ma20": _s0(ma20[last]),
        "macd_dif": _s0(macd_dif[last]), "macd_dea": _s0(macd_dea[last]), "macd_bar": _s0(macd_bar[last]),
        "kdj_k": _s0(k[last]), "kdj_d": _s0(d[last]), "kdj_j": _s0(j[last]),
        "rsi14": _s0(rsi[last]),
        "boll_upper": _s0(upper[last]), "boll_mid": _s0(mid[last]), "boll_lower": _s0(lower[last]),
        "atr14": _s0(atr[last]),
    }


def _s0(v):
    try:
        import numpy as np
        if v is None: return None
        fv = float(v)
        if np.isnan(fv): return None
        return round(fv, 4)
    except (ValueError, TypeError):
        return None
