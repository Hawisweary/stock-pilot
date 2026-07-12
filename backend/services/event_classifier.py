"""公告/新闻事件分类 — V5 新闻面子集（规则版 P3b）。"""
from __future__ import annotations

import sqlite3

import config

# event_type 枚举：fundamental 归基本面，不计入 news_event
# 顺序敏感：先匹配更具体/高优先级规则
EVENT_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("fundamental", ("业绩预告", "业绩快报", "季报", "季度报告", "年度报告", "年报", "半年报", "一季报", "三季报")),
    ("non_standard_audit", ("非标", "无法表示意见", "否定意见", "保留意见", "内控重大缺陷")),
    ("investigation", ("证监会立案", "立案调查", "被调查", "涉嫌违法", "立案告知书")),
    ("sell_down", ("减持", "清仓式减持", "拟减持", "减持计划", "减持公告")),
    ("buyback", ("股份回购", "回购股份", "回购A股", "回购公司", "集中竞价回购", "回购进展", "回购完成", "回购实施")),
    ("increase_holdings", ("增持计划", "增持股份", "举牌", "实际控制人增持", "控股股东增持")),
    ("contract", ("重大合同", "中标", "框架协议", "签订合同", "采购合同", "供货协议", "战略合作")),
    ("approval", ("获批", "核准", "注册", "上市许可", "临床试验", "药品注册", "生产许可")),
    ("equity_incentive", ("股权激励", "员工持股", "限制性股票", "股票期权", "持股计划")),
    ("dividend", ("利润分配", "分红派息", "现金分红", "派息实施", "利润分配预案")),
    ("subsidy", ("政府补助", "获得补助", "财政补贴", "税收返还", "补助公告")),
    ("institutional_research", (
        "机构调研", "调研活动", "投资者关系活动", "特定对象调研",
        "现场调研", "投资者调研", "调研记录",
    )),
    ("product_milestone", (
        "获订单", "获得订单", "中标公告", "批量交付", "量产下线", "产品上市", "顺利投产",
    )),
    ("management_change", (
        "高级管理人员离任", "董事长辞职", "总经理辞职", "监事辞职",
        "董事会秘书辞职", "总裁辞职", "财务总监辞职", "离任公告",
    )),
    ("performance_alert", ("业绩预亏", "预计亏损", "业绩大幅下滑", "亏损预警", "首亏", "由盈转亏")),
    ("litigation", ("诉讼", "仲裁", "行政处罚", "监管函", "违规", "警示函", "责令整改")),
    ("asset_sale", ("出售资产", "转让子公司", "资产处置", "出售股权", "剥离", "重大资产出售")),
]

# V5 新闻面计分映射（与 v5_scorer.POSITIVE_NEWS / NEGATIVE_NEWS 对齐）
NEWS_POSITIVE_TYPES = frozenset({
    "contract", "approval", "buyback", "increase_holdings",
    "equity_incentive", "dividend", "subsidy",
    "institutional_research", "product_milestone",
})
NEWS_NEGATIVE_TYPES = frozenset({
    "sell_down", "litigation", "investigation", "asset_sale",
    "non_standard_audit", "management_change", "performance_alert",
})

# V5 新闻面事件强度（净强度求和后 clip 到 [-2,+2]）
EVENT_INTENSITY: dict[str, int] = {
    "contract": 2,
    "approval": 2,
    "buyback": 1,
    "increase_holdings": 2,
    "equity_incentive": 1,
    "dividend": 1,
    "subsidy": 1,
    "institutional_research": 1,
    "product_milestone": 2,
    "sell_down": -2,
    "litigation": -1,
    "investigation": -2,
    "asset_sale": -1,
    "non_standard_audit": -2,
    "management_change": -1,
    "performance_alert": -2,
}


def event_intensity(event_type: str) -> int:
    return EVENT_INTENSITY.get((event_type or "").strip(), 0)

# 东财泛市场/板块类标题 — 不计入个股新闻面
NEWS_NOISE_MARKERS = (
    "[刷新]",
    "情绪加权",
    "主力资金",
    "概念涨",
    "概念下跌",
    "盘中播报",
    "突破年线",
    "筹码新动向",
    "筹码趋向集中",
    "跨越牛熊分界线",
    "长线走稳",
    "百元股阵营",
    "解密主力资金",
    "强势股追踪",
    "板块主力资金",
)


def classify_event_title(title: str) -> str:
    """根据标题返回 event_type，未命中返回空字符串。"""
    t = (title or "").strip()
    if not t:
        return ""
    if any(m in t for m in NEWS_NOISE_MARKERS):
        return ""
    for event_type, keywords in EVENT_RULES:
        if any(kw in t for kw in keywords):
            return event_type
    return ""


def _ensure_event_type_column(conn: sqlite3.Connection, table: str) -> None:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if "event_type" not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN event_type TEXT DEFAULT ''")


def classify_announcements(
    stock_ids: list[int] | None = None,
    *,
    limit_per_stock: int = 50,
    reclassify: bool = False,
) -> dict:
    """为 stock_announcements 回填 event_type。"""
    conn = sqlite3.connect(config.DB_PATH)
    try:
        _ensure_event_type_column(conn, "stock_announcements")
        if stock_ids:
            ph = ",".join("?" * len(stock_ids))
            stocks = conn.execute(
                f"SELECT id FROM stocks WHERE id IN ({ph}) AND is_active=1",
                stock_ids,
            ).fetchall()
        else:
            stocks = conn.execute("SELECT id FROM stocks WHERE is_active=1").fetchall()

        classified = 0
        scanned = 0
        for (sid,) in stocks:
            if reclassify:
                rows = conn.execute(
                    """SELECT id, title FROM stock_announcements
                       WHERE stock_id=? ORDER BY pub_date DESC LIMIT ?""",
                    (sid, limit_per_stock),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT id, title FROM stock_announcements
                       WHERE stock_id=? AND (event_type IS NULL OR event_type='')
                       ORDER BY pub_date DESC LIMIT ?""",
                    (sid, limit_per_stock),
                ).fetchall()
            for ann_id, title in rows:
                scanned += 1
                et = classify_event_title(title)
                if et:
                    conn.execute(
                        "UPDATE stock_announcements SET event_type=? WHERE id=?",
                        (et, ann_id),
                    )
                    classified += 1

        conn.commit()
        return {"stocks": len(stocks), "scanned": scanned, "classified": classified}
    finally:
        conn.close()


def classify_news(
    stock_ids: list[int] | None = None,
    *,
    limit_per_stock: int = 30,
    reclassify: bool = False,
) -> dict:
    """为 stock_news 回填 event_type（表存在时）。"""
    conn = sqlite3.connect(config.DB_PATH)
    try:
        if not conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='stock_news'"
        ).fetchone():
            return {"skipped": True, "reason": "stock_news table missing"}
        _ensure_event_type_column(conn, "stock_news")
        if stock_ids:
            ph = ",".join("?" * len(stock_ids))
            stocks = conn.execute(
                f"SELECT id FROM stocks WHERE id IN ({ph}) AND is_active=1",
                stock_ids,
            ).fetchall()
        else:
            stocks = conn.execute("SELECT id FROM stocks WHERE is_active=1").fetchall()

        classified = 0
        scanned = 0
        for (sid,) in stocks:
            if reclassify:
                rows = conn.execute(
                    """SELECT id, title FROM stock_news
                       WHERE stock_id=? ORDER BY pub_date DESC LIMIT ?""",
                    (sid, limit_per_stock),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT id, title FROM stock_news
                       WHERE stock_id=? AND (event_type IS NULL OR event_type='')
                       ORDER BY pub_date DESC LIMIT ?""",
                    (sid, limit_per_stock),
                ).fetchall()
            for nid, title in rows:
                scanned += 1
                et = classify_event_title(title)
                if et:
                    conn.execute(
                        "UPDATE stock_news SET event_type=? WHERE id=?",
                        (et, nid),
                    )
                    classified += 1

        conn.commit()
        return {"stocks": len(stocks), "scanned": scanned, "classified": classified}
    finally:
        conn.close()


def sync_event_classification(
    stock_ids: list[int] | None = None,
    *,
    reclassify: bool = False,
    use_llm: bool = True,
    llm_limit_per_stock: int = 12,
    llm_stock_ids: list[int] | None = None,
) -> dict:
    """公告 + 新闻分类：规则优先，LLM 补未命中项。"""
    ann = classify_announcements(stock_ids, reclassify=reclassify)
    news = classify_news(stock_ids, reclassify=reclassify)
    out: dict = {"announcements": ann, "news": news}
    if use_llm:
        llm_targets = llm_stock_ids if llm_stock_ids is not None else stock_ids
        if llm_targets is not None and len(llm_targets) == 0:
            out["llm"] = {"skipped": True, "reason": "no_llm_targets"}
        else:
            try:
                from services.event_classifier_llm import classify_events_llm

                out["llm"] = classify_events_llm(
                    llm_targets, limit_per_stock=llm_limit_per_stock
                )
                if llm_stock_ids is not None:
                    out["llm"]["stock_filter"] = llm_stock_ids
            except Exception as e:
                out["llm"] = {"error": str(e)}
    else:
        out["llm"] = {"skipped": True, "reason": "use_llm=False"}
    return out


def news_event_types() -> list[str]:
    """计入 V5 新闻面的 event_type（排除 fundamental）。"""
    return [t for t, _ in EVENT_RULES if t != "fundamental"]


def _announcement_events_sql(*, include_fundamental: bool) -> str:
    base = """SELECT title, ann_type, pub_date, event_type, source, 'announcement' AS kind
              FROM stock_announcements WHERE stock_id=? AND event_type != ''"""
    if not include_fundamental:
        base += " AND event_type != 'fundamental'"
    return base


def _news_events_sql(conn: sqlite3.Connection, *, include_fundamental: bool) -> str | None:
    news_cols = {r[1] for r in conn.execute("PRAGMA table_info(stock_news)").fetchall()}
    if "event_type" not in news_cols:
        return None
    src = "source" if "source" in news_cols else "'news'"
    base = f"""SELECT title, '' AS ann_type, pub_date, event_type, {src} AS source, 'news' AS kind
              FROM stock_news WHERE stock_id=? AND event_type != ''"""
    if not include_fundamental:
        base += " AND event_type != 'fundamental'"
    return base


def get_stock_events(
    stock_id: int,
    *,
    limit: int = 20,
    include_fundamental: bool = False,
) -> list[dict]:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        merged: list[dict] = []
        ann_cols = {r[1] for r in conn.execute("PRAGMA table_info(stock_announcements)").fetchall()}
        if "event_type" in ann_cols:
            sql = _announcement_events_sql(include_fundamental=include_fundamental)
            merged.extend(dict(r) for r in conn.execute(
                sql + " ORDER BY pub_date DESC LIMIT ?", (stock_id, limit)
            ).fetchall())

        if conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='stock_news'"
        ).fetchone():
            news_sql = _news_events_sql(conn, include_fundamental=include_fundamental)
            if news_sql:
                merged.extend(dict(r) for r in conn.execute(
                    news_sql + " ORDER BY pub_date DESC LIMIT ?", (stock_id, limit)
                ).fetchall())

        merged.sort(key=lambda r: r.get("pub_date") or "", reverse=True)
        return merged[:limit]
    finally:
        conn.close()
