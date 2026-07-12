"""政策事件同步 — 公告/新闻规则抽取 + T+20 行业超额响应。"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta

import config
from config import latest_trading_date
from services.market_index import fetch_index_kline
from services.policy_scorer import (
    MACRO_KEYWORDS,
    POLICY_INDUSTRY_BUCKET,
    POLICY_KEYWORDS,
    UNIVERSAL_NEGATIVE,
    UNIVERSAL_POSITIVE,
    keyword_scan,
)

POLICY_TITLE_MARKERS = (
    "国务院", "证监会", "央行", "人民银行", "发改委", "工信部",
    "财政部", "商务部", "国资委", "金融监管", "政治局", "中央经济工作会议",
)


def _policy_level_from_score(score: float) -> int:
    if score >= 20:
        return 2
    if score >= 8:
        return 1
    if score <= -20:
        return -2
    if score <= -8:
        return -1
    return 0


def _industries_from_title(title: str) -> list[str]:
    matched: set[str] = set()
    for ind in POLICY_KEYWORDS:
        _, hits = keyword_scan(title, ind)
        if hits:
            matched.add(ind)
    for kw, _ in UNIVERSAL_POSITIVE + UNIVERSAL_NEGATIVE:
        if kw in title:
            for kw_list in (MACRO_KEYWORDS["positive"], MACRO_KEYWORDS["negative"]):
                for mk, _ in kw_list:
                    if mk in title:
                        matched.add("宏观")
    if any(m in title for m in POLICY_TITLE_MARKERS):
        matched.add("宏观")
    return sorted(matched)


def _is_policy_title(title: str) -> bool:
    t = (title or "").strip()
    if not t:
        return False
    if any(m in t for m in POLICY_TITLE_MARKERS):
        return True
    for ind in POLICY_KEYWORDS:
        score, _ = keyword_scan(t, ind)
        if score != 0:
            return True
    for kw, _ in UNIVERSAL_POSITIVE + UNIVERSAL_NEGATIVE:
        if kw in t:
            return True
    for kw_list in (MACRO_KEYWORDS["positive"], MACRO_KEYWORDS["negative"]):
        for kw, _ in kw_list:
            if kw in t:
                return True
    return False


def sync_policy_events(
    *,
    lookback_days: int = 90,
    limit_per_source: int = 200,
) -> dict:
    """从公告/新闻标题抽取政策事件入库。"""
    since = (date.today() - timedelta(days=lookback_days)).isoformat()
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    added = 0
    scanned = 0
    try:
        ann_rows = conn.execute(
            """SELECT title, pub_date, source FROM stock_announcements
               WHERE pub_date >= ? ORDER BY pub_date DESC LIMIT ?""",
            (since, limit_per_source),
        ).fetchall()
        news_rows = []
        if conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='stock_news'"
        ).fetchone():
            news_cols = {r[1] for r in conn.execute("PRAGMA table_info(stock_news)")}
            src_col = "source" if "source" in news_cols else "'news'"
            news_rows = conn.execute(
                f"""SELECT title, pub_date, {src_col} AS source FROM stock_news
                   WHERE pub_date >= ? ORDER BY pub_date DESC LIMIT ?""",
                (since, limit_per_source),
            ).fetchall()

        seen: set[tuple[str, str]] = set()
        for row in list(ann_rows) + list(news_rows):
            title = str(row["title"] or "").strip()
            pub = str(row["pub_date"] or "")[:10]
            if not title or not pub:
                continue
            scanned += 1
            if not _is_policy_title(title):
                continue
            key = (pub, title[:80])
            if key in seen:
                continue
            seen.add(key)

            industries = _industries_from_title(title)
            level_score = 0.0
            for ind in industries:
                if ind == "宏观":
                    for kw_list in (
                        MACRO_KEYWORDS["positive"],
                        MACRO_KEYWORDS["negative"],
                    ):
                        for kw, w in kw_list:
                            if kw in title:
                                level_score += w
                    continue
                delta, _ = keyword_scan(title, ind)
                level_score += delta
            for kw, w in UNIVERSAL_POSITIVE:
                if kw in title:
                    level_score += w
            for kw, w in UNIVERSAL_NEGATIVE:
                if kw in title:
                    level_score += w

            level = _policy_level_from_score(level_score)
            if level == 0 and not industries:
                continue

            cur = conn.execute(
                """INSERT OR IGNORE INTO policy_events
                (pub_date, title, level, industries_json, source)
                VALUES (?,?,?,?,?)""",
                (
                    pub,
                    title[:300],
                    level,
                    json.dumps(industries, ensure_ascii=False),
                    str(row["source"] or "announcement"),
                ),
            )
            if cur.rowcount:
                added += 1
        conn.commit()
        return {"since": since, "scanned": scanned, "added": added}
    finally:
        conn.close()


def _index_return_20d(start_date: str) -> float | None:
    k = fetch_index_kline("sh000300", days=120, with_technical=False)
    bars = k.get("kline") or []
    if len(bars) < 25:
        return None
    dates = [str(b.get("date") or "")[:10] for b in bars]
    try:
        idx = next(i for i, d in enumerate(dates) if d >= start_date)
    except StopIteration:
        idx = 0
    if idx + 20 >= len(bars):
        return None
    c0 = bars[idx].get("close")
    c1 = bars[idx + 20].get("close")
    if not c0 or not c1:
        return None
    return (float(c1) - float(c0)) / float(c0) * 100


def _industry_stock_ids(conn: sqlite3.Connection, policy_ind: str) -> list[int]:
    """政策行业 key → 股票 id 列表。"""
    stock_cols = {r[1] for r in conn.execute("PRAGMA table_info(stocks)")}
    has_ind = "industry" in stock_cols
    if has_ind:
        rows = conn.execute(
            "SELECT id, industry_sw, industry FROM stocks WHERE is_active=1"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, industry_sw FROM stocks WHERE is_active=1"
        ).fetchall()
    ids: list[int] = []
    for row in rows:
        sid = int(row[0])
        sw = (row[1] or (row[2] if has_ind and len(row) > 2 else "") or "").strip()
        if not sw:
            continue
        bucket = POLICY_INDUSTRY_BUCKET.get(sw, sw)
        if policy_ind in (sw, bucket) or policy_ind == sw:
            ids.append(int(sid))
        elif policy_ind in POLICY_KEYWORDS and bucket == policy_ind:
            ids.append(int(sid))
    return ids


def _avg_stock_return_20d(
    conn: sqlite3.Connection, stock_ids: list[int], start_date: str
) -> float | None:
    if not stock_ids:
        return None
    rets: list[float] = []
    for sid in stock_ids[:40]:
        rows = conn.execute(
            """SELECT close FROM stock_daily_quotes
               WHERE stock_id=? AND trade_date >= ? AND close IS NOT NULL
               ORDER BY trade_date LIMIT 21""",
            (sid, start_date),
        ).fetchall()
        if len(rows) < 2:
            continue
        c0, c1 = float(rows[0][0]), float(rows[-1][0])
        if c0 <= 0:
            continue
        rets.append((c1 - c0) / c0 * 100)
    if not rets:
        return None
    return sum(rets) / len(rets)


def _coef_from_excess(excess: float) -> float:
    return max(0.5, min(1.5, 1.0 + excess / 10.0))


def compute_policy_industry_responses(*, min_age_days: int = 20) -> dict:
    """为已满 T+20 的政策事件计算行业超额与响应系数。"""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    computed = 0
    try:
        cutoff = (date.today() - timedelta(days=min_age_days)).isoformat()
        events = conn.execute(
            """SELECT id, pub_date, title, level, industries_json
               FROM policy_events WHERE pub_date <= ?
               ORDER BY pub_date DESC LIMIT 200""",
            (cutoff,),
        ).fetchall()

        bench_cache: dict[str, float | None] = {}
        for ev in events:
            eid = int(ev["id"])
            pub = str(ev["pub_date"])[:10]
            if bench_cache.get(pub) is None:
                bench_cache[pub] = _index_return_20d(pub)
            bench = bench_cache[pub]
            if bench is None:
                continue

            try:
                industries = json.loads(ev["industries_json"] or "[]")
            except json.JSONDecodeError:
                industries = []
            if not industries:
                industries = ["宏观"]

            for ind in industries:
                if conn.execute(
                    "SELECT 1 FROM policy_industry_response WHERE event_id=? AND industry_sw2=?",
                    (eid, ind),
                ).fetchone():
                    continue
                if ind == "宏观":
                    stock_ids = [
                        r[0]
                        for r in conn.execute(
                            "SELECT id FROM stocks WHERE is_active=1"
                        ).fetchall()
                    ]
                else:
                    stock_ids = _industry_stock_ids(conn, ind)
                ind_ret = _avg_stock_return_20d(conn, stock_ids, pub)
                if ind_ret is None:
                    continue
                excess = round(ind_ret - bench, 4)
                coef = round(_coef_from_excess(excess), 4)
                conn.execute(
                    """INSERT OR REPLACE INTO policy_industry_response
                    (event_id, industry_sw2, excess_return_20d, coef)
                    VALUES (?,?,?,?)""",
                    (eid, ind, excess, coef),
                )
                computed += 1
        conn.commit()
        return {"computed": computed, "events_checked": len(events)}
    finally:
        conn.close()


def sync_policy_v5() -> dict:
    """政策事件抽取 + T+20 响应一步完成。"""
    events = sync_policy_events()
    responses = compute_policy_industry_responses()
    return {"events": events, "responses": responses}


def get_policy_events(limit: int = 30) -> list[dict]:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT id, pub_date, title, level, industries_json, source
               FROM policy_events ORDER BY pub_date DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["industries"] = json.loads(d.pop("industries_json") or "[]")
            except json.JSONDecodeError:
                d["industries"] = []
            out.append(d)
        return out
    finally:
        conn.close()


def _stock_policy_industries(
    industry_sw: str | None,
    industry_sw2: str | None = None,
    industry_legacy: str | None = None,
) -> set[str]:
    """个股可匹配的政策行业集合（申万一级 + bucket + 二级）。"""
    from services.industry_normalize import normalize_industry

    out: set[str] = set()
    for raw in (industry_sw, industry_sw2, industry_legacy):
        if not raw:
            continue
        norm = normalize_industry(raw.strip())
        if not norm:
            continue
        out.add(norm)
        out.add(POLICY_INDUSTRY_BUCKET.get(norm, norm))
    return {x for x in out if x}


def _event_industries(industries_json: str | None) -> set[str]:
    try:
        items = json.loads(industries_json or "[]")
    except json.JSONDecodeError:
        return set()
    return {str(x).strip() for x in items if str(x).strip()}


def get_policy_score_v5_for_stock(stock_id: int) -> dict | None:
    """V5 政策乘数：仅统计与个股行业相关的事件 level × coef。"""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        stock_cols = {r[1] for r in conn.execute("PRAGMA table_info(stocks)")}
        cols = ["industry_sw"]
        if "industry_sw2" in stock_cols:
            cols.append("industry_sw2")
        if "industry" in stock_cols:
            cols.append("industry")
        row = conn.execute(
            f"SELECT {', '.join(cols)} FROM stocks WHERE id=?",
            (stock_id,),
        ).fetchone()
        if not row:
            return None
        sw = (row["industry_sw"] or "").strip()
        sw2 = (row["industry_sw2"] if "industry_sw2" in row.keys() else "") or ""
        legacy = (row["industry"] if "industry" in row.keys() else "") or ""
        stock_inds = _stock_policy_industries(sw, sw2, legacy)
        if not stock_inds:
            return {"stock_id": stock_id, "policy_score_v5": 0, "tier": 0, "events": []}

        since = (date.today() - timedelta(days=60)).isoformat()
        events = conn.execute(
            """SELECT e.id, e.pub_date, e.title, e.level, e.industries_json,
                      r.industry_sw2 AS resp_industry, r.excess_return_20d, r.coef
               FROM policy_events e
               INNER JOIN policy_industry_response r ON r.event_id=e.id
               WHERE e.pub_date >= ?
               ORDER BY e.pub_date DESC LIMIT 50""",
            (since,),
        ).fetchall()

        if not events:
            return {"stock_id": stock_id, "policy_score_v5": 0, "tier": 0, "events": []}

        scores: list[float] = []
        detail: list[dict] = []
        for ev in events:
            event_inds = _event_industries(ev["industries_json"])
            resp_ind = (ev["resp_industry"] or "").strip()
            resp_bucket = POLICY_INDUSTRY_BUCKET.get(resp_ind, resp_ind)
            if not event_inds & stock_inds:
                continue
            if resp_ind not in stock_inds and resp_bucket not in stock_inds:
                continue
            level = int(ev["level"] or 0)
            coef = float(ev["coef"] or 1.0)
            contrib = level * coef
            scores.append(contrib)
            detail.append(
                {
                    "pub_date": ev["pub_date"],
                    "title": ev["title"][:80],
                    "level": level,
                    "coef": coef,
                    "contrib": round(contrib, 2),
                    "industry": resp_ind,
                }
            )

        if not scores:
            return {"stock_id": stock_id, "policy_score_v5": 0, "tier": 0, "events": []}

        raw = max(scores)
        if raw >= 1.5:
            tier = 2
        elif raw >= 0.5:
            tier = 1
        elif raw <= -1.5:
            tier = -2
        elif raw <= -0.5:
            tier = -1
        else:
            tier = 0
        return {
            "stock_id": stock_id,
            "policy_score_v5": round(raw, 2),
            "tier": tier,
            "events": detail[:5],
        }
    finally:
        conn.close()
