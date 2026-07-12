"""
东财 Push2 批量适配器
- 换手率 + 量比 + 内外盘 三合一
- 支持批量（最多50只/次）
- 自动重试（指数退避）
"""
import json, time, requests
from typing import Optional

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
PUSH2_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
RETRY_MAX = 3

# 字段映射: f43=现价 f44=最高 f45=最低 f47=成交量 f48=成交额
#           f162=量比 f168=换手率 f772=内盘 f773=外盘
#           f170=涨跌幅 f100=名称
FIELDS = "f43,f44,f45,f47,f48,f100,f162,f168,f170,f772,f773"


def _to_secid(code: str) -> str:
    """股票代码 → 东财secid格式"""
    if code.startswith(("6", "9")):
        return f"1.{code}"
    elif code.startswith("8"):
        return f"2.{code}"
    return f"0.{code}"


def fetch_push2_batch(codes: list[str]) -> list[dict]:
    """
    批量获取东财实时行情
    返回: [{code, price, turnover_pct, liangbi, inside_vol, outside_vol, ...}]
    """
    secids = ",".join(_to_secid(c) for c in codes)
    url = f"{PUSH2_URL}?secids={secids}&fields={FIELDS}"
    print(f'[东财Push2] 批量请求{len(codes)}只')

    last_err = None
    for attempt in range(RETRY_MAX):
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=5)
            data = resp.json()
            items = data.get("data", []) if isinstance(data, dict) else []
            if not items:
                raise ValueError("Empty response")
            result = []
            for item in items:
                code = item.get("code", "")
                name = item.get("f100", "")
                price = item.get("f43", 0) or 0
                turnover_pct = item.get("f168")
                liangbi = item.get("f162")
                inside_vol = item.get("f772")
                outside_vol = item.get("f773")
                change_pct = item.get("f170", 0) or 0
                volume = item.get("f47", 0) or 0
                high = item.get("f44", 0) or 0
                low = item.get("f45", 0) or 0

                # 清洗数据类型
                def to_float(v):
                    try: return round(float(v), 2)
                    except: return 0.0
                def to_int(v):
                    try: return int(v)
                    except: return 0

                result.append({
                    "code": code, "name": name,
                    "price": to_float(price),
                    "change_pct": to_float(change_pct),
                    "volume": to_int(volume),
                    "turnover_pct": to_float(turnover_pct),
                    "liangbi": to_float(liangbi),
                    "inside_vol": to_int(inside_vol),
                    "outside_vol": to_int(outside_vol),
                    "high": to_float(high),
                    "low": to_float(low),
                })
            print(f'[东财Push2] 成功返回{len(result)}只')
            return result

        except (requests.ConnectionError, requests.Timeout, ValueError, json.JSONDecodeError) as e:
            last_err = e
            if attempt < RETRY_MAX - 1:
                delay = 0.5 * (attempt + 1)
                print(f'[东财Push2] 失败(重试{attempt+1}/{RETRY_MAX}): {str(e)[:40]}, {delay}s后重试')
                time.sleep(delay)
            continue

    print(f'[东财Push2] 最终失败: {last_err}')
    return []


def push2_realtime(codes: list[str]) -> dict[str, dict]:
    """东财批量行情 → {code: {}} 格式，与 tencent_quote 兼容"""
    items = fetch_push2_batch(codes)
    result = {}
    for item in items:
        code = item.pop("code", "")
        if code:
            result[code] = item
    return result
