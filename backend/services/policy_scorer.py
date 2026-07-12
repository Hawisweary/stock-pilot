"""政策面评分引擎 — 关键词规则 + LLM验证 + 多行业加权"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, timedelta

from config import DB_PATH
from services.industry_normalize import normalize_industry

POLICY_NEWS_LOOKBACK_DAYS = 30

# 申万一级 → 政策词库 key
POLICY_INDUSTRY_BUCKET: dict[str, str] = {
    "计算机硬件": "计算机",
    "计算机设备": "计算机",
    "计算机应用": "计算机",
    "软件": "计算机",
    "通信": "通信设备",
    "航空装备Ⅱ": "国防军工",
    "航空装备": "国防军工",
    "航空航天": "国防军工",
    "国防军工": "国防军工",
    "工程咨询服务Ⅱ": "建筑装饰",
    "工程咨询服务": "建筑装饰",
    "专用设备": "机械设备",
    "通用设备": "机械设备",
    "自动化设备": "机械设备",
    "元件": "电子",
    "半导体": "电子",
    "光学光电子": "电子",
    "消费电子": "电子",
    "化学制品": "基础化工",
    "化学制药": "医药生物",
    "电网设备": "电力设备",
    "电池": "电力设备",
    "光伏设备": "电力设备",
    "风电设备": "电力设备",
    "汽车零部件": "汽车",
    "汽车整车": "汽车",
    "传媒": "传媒",
    # 东财 F10 二级行业名
    "白酒": "食品饮料",
    "汽车零部件": "汽车",
    "通信传输设备": "通信设备",
    "其他软件服务": "计算机",
    "地面装备": "国防军工",
    "其他化学制品": "基础化工",
    "半导体设备": "电子",
    "消费电子零部件": "电子",
}

POLICY_KEYWORDS: dict[str, dict] = {
    "国防军工": {
        "positive": [
            ("军民融合", 15), ("国防预算", 15), ("低空经济", 20), ("无人机", 10),
            ("军工", 12), ("装备采购", 12), ("航天", 10), ("卫星", 10),
            ("政策支持", 10), ("补贴", 8), ("试点", 8),
        ],
        "negative": [("削减预算", -20), ("出口管制", -12)],
    },
    "汽车": {
        "positive": [
            ("新能源", 15), ("汽车下乡", 18), ("购置税", 15), ("充电桩", 10),
            ("智能驾驶", 12), ("以旧换新", 15), ("补贴", 12), ("免征", 15),
            ("收购", 8), ("获批", 8),
        ],
        "negative": [("限购", -15), ("召回", -12), ("反补贴", -18)],
    },
    "机械设备": {
        "positive": [
            ("人形机器人", 20), ("智能制造", 15), ("工业母机", 18), ("设备更新", 15),
            ("自动化", 10), ("机器人", 15), ("高端装备", 12),
        ],
        "negative": [("产能过剩", -15), ("反倾销", -18)],
    },
    "计算机": {
        "positive": [
            ("数字经济", 15), ("信创", 18), ("人工智能", 18), ("算力", 15),
            ("国产替代", 18), ("云计算", 10), ("大模型", 15), ("软件", 8),
        ],
        "negative": [("监管收紧", -15), ("网络安全审查", -10)],
    },
    "银行": {
        "positive": [("降准", 15), ("降息", 10), ("金融改革", 10), ("分红", 8)],
        "negative": [("银行让利", -15), ("不良率", -10), ("严格监管", -12)],
    },
    "房地产": {
        "positive": [("放松限购", 20), ("降首付", 18), ("保交楼", 15), ("城中村改造", 15)],
        "negative": [("严控", -18), ("房地产税", -20), ("三条红线", -20)],
    },
    "医药生物": {
        "positive": [("创新药", 15), ("审批加速", 12), ("医保谈判", 8)],
        "negative": [("集采", -18), ("控费", -15), ("飞行检查", -12)],
    },
    "电子": {
        "positive": [
            ("半导体", 18), ("芯片", 15), ("国产替代", 18), ("集成电路", 18),
            ("光刻", 15), ("存储", 10), ("GPU", 12),
        ],
        "negative": [("出口管制", -18), ("实体清单", -20)],
    },
    "通信设备": {
        "positive": [
            ("5G", 15), ("6G", 18), ("新基建", 15), ("光纤", 10),
            ("数据中心", 12), ("算力", 15), ("网络强国", 10),
        ],
        "negative": [("频谱拍卖", -8), ("管制", -10)],
    },
    "电力设备": {
        "positive": [
            ("新型电力系统", 18), ("特高压", 15), ("储能", 15), ("新能源", 12),
            ("电网投资", 10), ("充电桩", 12),
        ],
        "negative": [("产能过剩", -15), ("限电", -10)],
    },
    "家用电器": {
        "positive": [("以旧换新", 20), ("家电下乡", 18), ("消费券", 10), ("补贴", 8)],
        "negative": [("贸易摩擦", -12), ("反倾销", -10)],
    },
    "基础化工": {
        "positive": [("化工", 10), ("新材料", 12), ("绿色化工", 10)],
        "negative": [("环保限产", -12), ("安全事故", -15)],
    },
    "传媒": {
        "positive": [
            ("数字经济", 12), ("文化消费", 10), ("元宇宙", 10), ("数字创意", 10),
            ("人工智能", 12), ("政策支持", 8),
        ],
        "negative": [("严监管", -12), ("整顿", -10)],
    },
    "建筑装饰": {
        "positive": [("基建", 12), ("专项债", 10), ("城市更新", 12), ("规划", 8)],
        "negative": [("调控", -10), ("收紧", -8)],
    },
}

MACRO_KEYWORDS = {
    "positive": [
        ("降准", 5), ("降息", 5), ("减税降费", 8), ("专项债", 5),
        ("财政刺激", 8), ("稳增长", 5),
    ],
    "negative": [("加息", -8), ("收紧货币", -8), ("去杠杆", -5)],
}

UNIVERSAL_POSITIVE = [
    ("政策利好", 10), ("产业扶持", 12), ("重点支持", 12),
    ("规划出台", 10), ("专项资金", 10),
]
UNIVERSAL_NEGATIVE = [
    ("严监管", -15), ("限制准入", -15), ("整顿", -12), ("罚单", -10),
]


def _resolve_policy_industry(raw: str, conn: sqlite3.Connection | None = None) -> str:
    sw = normalize_industry(raw, conn) if raw else ""
    if not sw:
        return ""
    if sw in POLICY_KEYWORDS:
        return sw
    if sw in POLICY_INDUSTRY_BUCKET:
        bucket = POLICY_INDUSTRY_BUCKET[sw]
        return bucket if bucket in POLICY_KEYWORDS else sw
    low = re.sub(r"[\s&]+", "", sw.lower())
    eng_map = {
        "aerospace&defense": "国防军工",
        "computerhardware": "计算机",
        "specialtyindustrialmachinery": "机械设备",
        "specialtychemicals": "基础化工",
        "electricalequipment&parts": "电力设备",
        "communicationequipment": "通信设备",
        "furnishingsfixtures&appliances": "家用电器",
    }
    for eng, chn in eng_map.items():
        if eng in low:
            return chn if chn in POLICY_KEYWORDS else POLICY_INDUSTRY_BUCKET.get(chn, chn)
    return sw


def get_industries_for_stock(stock_id: int) -> list[tuple[str, float]]:
    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("PRAGMA busy_timeout=120000")
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT industry, industry_sw, industry_sw2 FROM stocks WHERE id=?", (stock_id,)
    ).fetchone()
    conn.close()

    if not row:
        return [("其他", 1.0)]

    raw = (row["industry_sw2"] or row["industry_sw"] or row["industry"] or "").strip()
    if raw in ("-", "—", "None"):
        raw = (row["industry_sw"] or row["industry"] or "").strip()
    if not raw:
        return [("其他", 1.0)]

    sw = _resolve_policy_industry(raw)
    policy_key = sw if sw in POLICY_KEYWORDS else POLICY_INDUSTRY_BUCKET.get(sw, sw)
    if policy_key in POLICY_KEYWORDS:
        return [(policy_key, 1.0)]
    if sw:
        return [(sw, 1.0)]
    return [("其他", 1.0)]


def keyword_scan(news_text: str, industry: str) -> tuple[float, list[dict]]:
    lookup = industry if industry in POLICY_KEYWORDS else POLICY_INDUSTRY_BUCKET.get(industry, industry)
    rules = POLICY_KEYWORDS.get(lookup, {})
    score = 0.0
    matched: list[dict] = []

    for kw_list in (rules.get("positive", []), rules.get("negative", [])):
        for kw, weight in kw_list:
            if kw in news_text:
                score += weight
                matched.append({"keyword": kw, "weight": weight, "industry": lookup})

    for kw_list in (MACRO_KEYWORDS["positive"], MACRO_KEYWORDS["negative"]):
        for kw, weight in kw_list:
            if kw in news_text:
                score += weight
                matched.append({"keyword": kw, "weight": weight, "macro": True})

    for kw, weight in UNIVERSAL_POSITIVE:
        if kw in news_text:
            score += weight
            matched.append({"keyword": kw, "weight": weight, "universal": True})
    for kw, weight in UNIVERSAL_NEGATIVE:
        if kw in news_text:
            score += weight
            matched.append({"keyword": kw, "weight": weight, "universal": True})

    return score, matched


def _keyword_score_from_news(
    conn: sqlite3.Connection, stock_id: int, keyword_total: float, has_news: bool
) -> float:
    if keyword_total != 0:
        return max(0.0, min(100.0, 50 + keyword_total * 0.5))
    if not has_news:
        return 50.0
    since = (date.today() - timedelta(days=POLICY_NEWS_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    row = conn.execute(
        """SELECT AVG(sentiment_score) FROM stock_news
           WHERE stock_id=? AND sentiment_score IS NOT NULL
           AND pub_date >= ?""",
        (stock_id, since),
    ).fetchone()
    if row and row[0] is not None:
        return max(0.0, min(100.0, float(row[0])))
    return 50.0


def compute_policy_score(stock_id: int, code: str, *, use_llm: bool = True) -> dict:
    today = date.today().strftime("%Y-%m-%d")
    industries = get_industries_for_stock(stock_id)

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("PRAGMA busy_timeout=120000")
    conn.row_factory = sqlite3.Row

    since = (date.today() - timedelta(days=POLICY_NEWS_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    news_rows = conn.execute(
        """SELECT title FROM stock_news WHERE stock_id=?
           AND pub_date >= ? ORDER BY pub_date DESC LIMIT 30""",
        (stock_id, since),
    ).fetchall()

    all_titles = " ".join(r["title"] for r in news_rows)

    keyword_total = 0.0
    all_matches: list[dict] = []
    for ind, w in industries:
        delta, matches = keyword_scan(all_titles, ind)
        keyword_total += delta * w
        all_matches.extend(matches)

    seen: set[str] = set()
    unique_matches = []
    for m in all_matches:
        k = m["keyword"]
        if k not in seen:
            seen.add(k)
            unique_matches.append(m)

    keyword_score = _keyword_score_from_news(conn, stock_id, keyword_total, bool(news_rows))

    llm_score = None
    llm_summary = ""
    llm_confidence = 0.0
    try:
        from services.news_fetcher import is_llm_available, chat_completion

        if use_llm and is_llm_available() and news_rows:
            ind_labels = ", ".join(f"{i} ({w*100:.0f}%)" for i, w in industries)
            titles_snippet = "\n".join(r["title"][:60] for r in news_rows[:10])
            prompt = f"""判断以下股票近期政策倾向，返回JSON：
股票行业: {ind_labels}
近期新闻:
{titles_snippet}

{{"tendency": "强力支持/支持/中性/限制/强力限制",
 "score": 0-100,
 "confidence": 0-1,
 "summary": "20字以内",
 "reason": "简短理由"}}"""
            text = chat_completion(
                prompt, system_prompt="你是中国政策分析专家。", max_tokens=300, temperature=0.1
            )
            start, end = text.find("{"), text.rfind("}") + 1
            if 0 <= start < end:
                policy_result = json.loads(text[start:end])
                llm_score = policy_result.get("score", 50)
                llm_summary = policy_result.get("summary", "")
                llm_confidence = float(policy_result.get("confidence", 0.5))
    except Exception:
        pass

    subsidy_score = 50.0
    try:
        import akshare as ak

        df = ak.stock_gpzy_em(date=today.split("-")[0])
        if df is not None and not df.empty:
            row = df[df["代码"] == code]
            if not row.empty:
                subsidy_str = str(row.iloc[0].get("补助金额", "0"))
                nums = re.findall(r"[\d.]+", subsidy_str)
                amount = float(nums[0]) if nums else 0
                if amount > 1000:
                    subsidy_score = 80
                elif amount > 500:
                    subsidy_score = 70
                elif amount > 100:
                    subsidy_score = 60
    except Exception:
        pass

    if llm_score is not None and llm_confidence >= 0.3:
        composite = keyword_score * 0.6 + llm_score * 0.3 + subsidy_score * 0.1
    else:
        composite = keyword_score * 0.7 + subsidy_score * 0.3
        if not llm_summary:
            llm_summary = "LLM未使用（离线或低置信度）"

    composite = round(composite, 1)

    conn.execute(
        """CREATE TABLE IF NOT EXISTS policy_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT, stock_id INTEGER NOT NULL,
            date TEXT NOT NULL, composite_score REAL, keyword_score REAL,
            llm_score REAL, subsidy_score REAL, breakdown_json TEXT,
            UNIQUE(stock_id, date)
        )"""
    )
    conn.execute(
        """INSERT OR REPLACE INTO policy_scores
           (stock_id, date, composite_score, keyword_score, llm_score, subsidy_score, breakdown_json)
           VALUES (?,?,?,?,?,?,?)""",
        (
            stock_id,
            today,
            composite,
            round(keyword_score, 1),
            round(llm_score, 1) if llm_score is not None else None,
            subsidy_score,
            json.dumps(
                {
                    "keywords": unique_matches[:10],
                    "llm_summary": llm_summary,
                    "llm_confidence": llm_confidence,
                    "industries": [(i, w) for i, w in industries],
                    "keyword_total": keyword_total,
                },
                ensure_ascii=False,
            ),
        ),
    )
    conn.commit()
    conn.close()

    return {
        "stock_id": stock_id,
        "code": code,
        "date": today,
        "composite_score": composite,
        "keyword_score": round(keyword_score, 1),
        "llm_score": round(llm_score, 1) if llm_score is not None else None,
        "subsidy_score": subsidy_score,
        "summary": llm_summary,
        "keywords": unique_matches[:10],
        "industries": [(i, w) for i, w in industries],
        "confidence": llm_confidence,
    }
