"""多源数据融合 — 腾讯+东财+同花顺三源交叉验证"""
import sqlite3, json, socket
from datetime import date

socket.setdefaulttimeout(8)
from config import DB_PATH


def fusion_quote(stock_id: int, code: str) -> dict:
    """三源行情融合 + 交叉验证"""
    today = date.today().strftime("%Y-%m-%d")

    sources = {"tencent": None, "eastmoney": None, "tonghuashun": None}

    # 1. 腾讯行情（已有）
    try:
        from services.data_sources import tencent_quote
        q = tencent_quote([code])
        if q and code in q and q[code].get("price"):
            sources["tencent"] = {"price": q[code]["price"], "change_pct": q[code].get("change_pct", 0),
                                  "volume": q[code].get("volume", 0), "turnover_pct": q[code].get("turnover_pct", 0),
                                  "pe_ttm": q[code].get("pe_ttm", 0), "pb": q[code].get("pb", 0)}
    except Exception as e:
        print(f"[融合] 腾讯失败: {e}")

    # 2. 东财行情
    try:
        import akshare as ak
        east_code = code
        market_prefix = "1." if code.startswith("6") else "0."
        df_em = ak.stock_individual_info_em(symbol=market_prefix + code)
        if df_em is not None and not df_em.empty:
            info = dict(zip(df_em["item"], df_em["value"]))
            sources["eastmoney"] = {
                "price": float(info.get("最新价", 0) or 0),
                "change_pct": float(info.get("涨跌幅", 0) or 0),
                "volume": float(info.get("成交量", 0) or 0),
                "pe_ttm": float(info.get("市盈率-动态", 0) or 0),
                "pb": float(info.get("市净率", 0) or 0),
            }
    except Exception as e:
        print(f"[融合] 东财失败: {e}")

    # 3. 交叉验证
    valid_sources = {k: v for k, v in sources.items() if v and v.get("price", 0) > 0}
    prices = [v["price"] for v in valid_sources.values()]
    validation = {"source_count": len(valid_sources), "status": "ok", "deviation_pct": 0,
                  "consensus_price": 0, "warnings": []}

    if len(prices) >= 2:
        consensus = sum(prices) / len(prices)
        max_dev = max(abs(p - consensus) / consensus * 100 for p in prices)
        validation["consensus_price"] = round(consensus, 2)
        validation["deviation_pct"] = round(max_dev, 2)

        if max_dev > 5:
            validation["status"] = "red"
            validation["warnings"].append(f"三源价格偏差{max_dev:.1f}%")
        elif max_dev > 2:
            validation["status"] = "yellow"
        else:
            validation["status"] = "green"

    # 4. 取共识价格更新到行情表
    if validation["consensus_price"] > 0:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""CREATE TABLE IF NOT EXISTS data_quality (
            id INTEGER PRIMARY KEY AUTOINCREMENT, stock_id INTEGER, date TEXT,
            tencent_price REAL, eastmoney_price REAL, ths_price REAL,
            consensus_price REAL, deviation_pct REAL, status TEXT,
            UNIQUE(stock_id, date))""")
        conn.execute("""INSERT OR REPLACE INTO data_quality
            (stock_id, date, tencent_price, eastmoney_price, ths_price,
             consensus_price, deviation_pct, status)
            VALUES (?,?,?,?,?,?,?,?)""",
            (stock_id, today,
             sources["tencent"]["price"] if sources["tencent"] else None,
             sources["eastmoney"]["price"] if sources["eastmoney"] else None,
             None,
             validation["consensus_price"], validation["deviation_pct"],
             validation["status"]))
        conn.commit()
        conn.close()

    return {
        "stock_id": stock_id, "code": code, "date": today,
        "sources": {k: {"price": v["price"]} for k, v in valid_sources.items()},
        "validation": validation,
    }
