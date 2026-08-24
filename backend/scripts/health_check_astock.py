"""A股实时接口健康巡检。akshare/东财常悄悄改接口,而调用处遍地 except: return [] 会静默吞错,
导致功能"界面在、其实返空"。本脚本定期对关键接口跑一遍,发现失效即记录/告警。

用法:venv-quant/bin/python backend/scripts/health_check_astock.py
接入 weekly 调度;结果写 afr.db.astock_health_check 表 + 返回码(有失效则非0)。
"""
from __future__ import annotations

import sqlite3
import sys
import warnings
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DB_PATH  # noqa: E402

# 多只活跃大盘股;短路——任一只返数据即判该接口正常
CODES = ["600519", "000001", "300750", "002594", "601318", "600036", "000858", "601857"]
# 事件型接口(解禁/大宗/龙虎榜)对具体股可能合法为空,全空只记 info 不算故障
EVENT_BASED = {"unlock_calendar", "block_trade", "dragon_tiger_board"}


def _len(v) -> int:
    if isinstance(v, dict):
        for k in ("forecasts", "history", "unlocks", "trades", "list"):
            if isinstance(v.get(k), list):
                return len(v[k])
        return 1 if v else 0
    return len(v) if hasattr(v, "__len__") else (1 if v else 0)


def _checks():
    import services.astock_data as A
    import services.data_sources as D
    return [
        ("concept_boards", A.concept_boards), ("dragon_tiger_board", A.dragon_tiger_board),
        ("unlock_calendar", A.unlock_calendar), ("margin_trading", A.margin_trading),
        ("block_trade", A.block_trade), ("shareholder_change", A.shareholder_change),
        ("dividend_history", A.dividend_history), ("sina_financial_report", A.sina_financial_report),
        ("consensus_eps", A.consensus_eps), ("eastmoney_stock_info", D.eastmoney_stock_info),
        ("em_margin_trading", D.margin_trading), ("em_dividend_history", D.dividend_history),
        ("eastmoney_stock_news", D.eastmoney_stock_news),
    ]


def run() -> int:
    results = []
    for name, fn in _checks():
        best, err = 0, None
        for c in CODES:
            try:
                best = max(best, _len(fn(c)))
            except Exception as e:  # noqa: BLE001
                err = repr(e)[:80]
            if best > 0:
                break  # 短路
        if best > 0:
            status = "ok"
        elif err:
            status = "broken"  # 抛异常 = 明确坏了
        elif name in EVENT_BASED:
            status = "empty_ok"  # 事件型全空可能合法
        else:
            status = "suspect"  # 应有数据却全空
        results.append((name, status, best, err))

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("""CREATE TABLE IF NOT EXISTS astock_health_check (
        check_time TEXT, func TEXT, status TEXT, max_rows INT, err TEXT)""")
    now = datetime.now().isoformat(timespec="seconds")
    conn.executemany("INSERT INTO astock_health_check VALUES (?,?,?,?,?)",
                     [(now, n, s, b, e) for n, s, b, e in results])
    conn.commit()
    conn.close()

    bad = [r for r in results if r[1] in ("broken", "suspect")]
    icon = {"ok": "✅", "broken": "❌", "suspect": "⚠️", "empty_ok": "·"}
    print(f"[A股接口巡检 {now}]")
    for n, s, b, e in results:
        print(f"  {icon[s]} {n}: {s} (max={b}){' ' + e if e else ''}")
    print(f"故障/可疑: {len(bad)}/{len(results)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(run())
