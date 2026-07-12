"""
种子数据脚本 - 插入初始测试股票
"""
import sys
import os
# 添加 backend 目录到 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from database import init, get

# 测试股票列表
TEST_STOCKS = [
    {"code": "600519", "name": "贵州茅台", "market": "A", "industry": "白酒"},
    {"code": "000858", "name": "五粮液",   "market": "A", "industry": "白酒"},
    {"code": "600036", "name": "招商银行", "market": "A", "industry": "银行"},
    {"code": "000651", "name": "格力电器", "market": "A", "industry": "家电"},
    {"code": "601318", "name": "中国平安", "market": "A", "industry": "保险"},
]


def seed():
    """插入种子数据"""
    conn = init()
    cur = conn.cursor()

    inserted = 0
    for stock in TEST_STOCKS:
        existing = cur.execute(
            "SELECT id FROM stocks WHERE code=? AND market=?",
            (stock["code"], stock["market"])
        ).fetchone()

        if existing:
            print(f"  已存在: {stock['code']} {stock['name']}")
            continue

        cur.execute(
            """INSERT INTO stocks (code, name, market, industry)
               VALUES (?, ?, ?, ?)""",
            (stock["code"], stock["name"], stock["market"], stock["industry"])
        )
        inserted += 1
        print(f"  新增: {stock['code']} {stock['name']}")

    conn.commit()
    print(f"\n种子数据插入完成: 新增 {inserted} 只股票")
    return inserted


if __name__ == "__main__":
    seed()
