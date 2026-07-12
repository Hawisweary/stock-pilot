"""
行业名称归一化 -> 申万一级 (industry_sw)
"""
from __future__ import annotations

import re
import sqlite3

# 英文 yfinance / 常见中文别名 -> 申万一级
INDUSTRY_ALIASES: dict[str, str] = {
    "consumer staples": "食品饮料",
    "food & beverage": "食品饮料",
    "beverages": "食品饮料",
    "白酒": "食品饮料",
    "啤酒": "食品饮料",
    "technology": "电子",
    "information technology": "电子",
    "semiconductors": "电子",
    "software": "计算机",
    "internet content & information": "传媒",
    "financial services": "银行",
    "banks": "银行",
    "insurance": "非银金融",
    "real estate": "房地产",
    "healthcare": "医药生物",
    "pharmaceuticals": "医药生物",
    "biotechnology": "医药生物",
    "energy": "石油石化",
    "oil & gas": "石油石化",
    "utilities": "公用事业",
    "industrials": "机械设备",
    "materials": "基础化工",
    "basic materials": "基础化工",
    "communication services": "通信",
    "telecom": "通信",
    "automobiles": "汽车",
    "auto": "汽车",
    "auto parts": "汽车",
    "specialty industrial machinery": "机械设备",
    "industrial machinery": "机械设备",
    "electrical equipment": "电力设备",
    "electrical equipment & parts": "电力设备",
    "consumer discretionary": "商贸零售",
    "retail": "商贸零售",
    "家用电器": "家用电器",
    "电力设备": "电力设备",
    "新能源": "电力设备",
    "光伏": "电力设备",
    "军工": "国防军工",
    "国防军工": "国防军工",
    "交通运输": "交通运输",
    "煤炭": "煤炭",
    "钢铁": "钢铁",
    "有色金属": "有色金属",
    "建筑": "建筑装饰",
    "建筑装饰": "建筑装饰",
    "农林牧渔": "农林牧渔",
    "纺织服饰": "纺织服饰",
    "轻工制造": "轻工制造",
    "环保": "环保",
    "美容护理": "美容护理",
    "社会服务": "社会服务",
    # 东财/新浪常用中文行业名映射
    "军工电子": "国防军工",
    "光学光电子": "电子",
    "计算机应用": "计算机",
    "计算机设备": "计算机",
    "汽车零部件": "汽车",
    "汽车整车": "汽车",
    "自动化设备": "机械设备",
    "通用设备": "机械设备",
    "专用设备": "机械设备",
    "电子化学品": "电子",
    "半导体": "电子",
    "元件": "电子",
    "消费电子": "电子",
    "通信设备": "通信",
    "通信服务": "通信",
    "化学制品": "基础化工",
    "化学制药": "医药生物",
    "computerhardware": "计算机",
    "computer hardware": "计算机",
    "aerospace&defense": "国防军工",
    "aerospace & defense": "国防军工",
    "specialtyindustrialmachinery": "机械设备",
    "specialty industrial machinery": "机械设备",
    "specialtychemicals": "基础化工",
    "specialty chemicals": "基础化工",
    "electricalequipment&parts": "电力设备",
    "electrical equipment & parts": "电力设备",
    "furnishings,fixtures&appliances": "家用电器",
    "furnishings, fixtures & appliances": "家用电器",
    "household appliances": "家用电器",
    "communicationequipment": "通信",
    "communication equipment": "通信",
    "工程咨询服务Ⅱ": "建筑装饰",
    "工程咨询服务": "建筑装饰",
    "航空装备Ⅱ": "国防军工",
    "航空装备": "国防军工",
    "航天装备Ⅱ": "国防军工",
    "航天装备": "国防军工",
    "家电零部件Ⅱ": "机械设备",
    "家电零部件": "家用电器",
    "专业工程": "建筑装饰",
    "信息传输、软件和信息技术服务业": "计算机",
    "软件和信息技术服务业": "计算机",
    "electroniccomponents": "电子",
    "electronic components": "电子",
    "计算机硬件": "计算机",
    "计算机应用": "计算机",
    "计算机设备": "计算机",
    "软件服务": "计算机",
    "软件开发": "计算机",
    "IT服务": "计算机",
    "传媒": "传媒",
    "数字媒体": "传媒",
    "广告营销": "传媒",
    "中药": "医药生物",
    "医疗器械": "医药生物",
    "医疗服务": "医药生物",
    "生物制品": "医药生物",
    "电网设备": "电力设备",
    "电池": "电力设备",
    "风电设备": "电力设备",
    "光伏设备": "电力设备",
}

SW_L1_LIST = sorted(set(INDUSTRY_ALIASES.values()))


def normalize_industry(raw: str | None, conn: sqlite3.Connection | None = None) -> str:
    """将原始行业名映射为申万一级；无法映射时返回清洗后的原文"""
    if not raw:
        return ""
    text = str(raw).strip()
    if not text:
        return ""

    if text in SW_L1_LIST:
        return text

    low = text.lower()
    if low in INDUSTRY_ALIASES:
        return INDUSTRY_ALIASES[low]

    if conn:
        row = conn.execute(
            "SELECT industry_sw FROM industry_aliases WHERE raw_name=?",
            (text,),
        ).fetchone()
        if row:
            return row["industry_sw"]

    # 子串匹配（优先长字符串匹配，避免"电子"误判"军工电子"）
    for alias, sw in sorted(INDUSTRY_ALIASES.items(), key=lambda x: -len(x[0])):
        if len(alias) >= 2 and alias in low:
            return sw
    for sw in sorted(SW_L1_LIST, key=len, reverse=True):
        if sw in text:
            return sw

    return re.sub(r"\s+", "", text)[:32]
