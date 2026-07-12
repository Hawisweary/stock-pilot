"""V5 公告/新闻事件 LLM 分类 — 规则未命中时补漏（P1）。"""
from __future__ import annotations

import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import config
from services.event_classifier import EVENT_RULES
from services.llm_client import chat_completion, is_llm_available

VALID_EVENT_TYPES = frozenset(t for t, _ in EVENT_RULES)

_EVENT_TYPE_GUIDE = """
event_type 枚举（只选其一，无关/泛市场新闻用空字符串 ""）：
- fundamental: 财报/业绩预告/季报年报（不计入新闻面，但可标注）
- contract: 重大合同、中标、战略合作
- approval: 获批、核准、注册
- buyback: 股份回购
- increase_holdings: 增持、举牌
- equity_incentive: 股权激励、员工持股
- dividend: 分红派息
- subsidy: 政府补助
- institutional_research: 机构调研、投资者关系活动
- product_milestone: 获订单、量产、产品上市
- management_change: 高管离任、辞职
- performance_alert: 业绩预亏、由盈转亏
- sell_down: 减持
- investigation: 证监会立案、立案调查
- litigation: 诉讼、处罚、警示函
- asset_sale: 出售资产、剥离
- non_standard_audit: 审计非标意见
无关标题（板块涨跌、主力资金、行情播报、名单汇总无公司事件）→ ""
"""


def _parse_llm_batch(raw: str, n: int) -> list[str]:
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return [""] * n
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return [""] * n

    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return [""] * n

    out = [""] * n
    for item in items:
        if not isinstance(item, dict):
            continue
        idx = int(item.get("idx", 0)) - 1
        if not (0 <= idx < n):
            continue
        et = str(item.get("event_type") or "").strip()
        if et not in VALID_EVENT_TYPES:
            et = ""
        out[idx] = et
    return out


def classify_titles_llm(titles: list[str]) -> list[str]:
    """批量 LLM 分类，返回与 titles 等长的 event_type 列表。"""
    if not titles:
        return []
    if not is_llm_available():
        raise RuntimeError("LLM 未配置")

    numbered = "\n".join(f"{i + 1}. {t[:200]}" for i, t in enumerate(titles))
    prompt = f"""对下列 A 股公告/新闻标题分类，返回纯 JSON：
{{
  "items": [
    {{"idx": 1, "event_type": "buyback"}},
    {{"idx": 2, "event_type": ""}}
  ]
}}
{_EVENT_TYPE_GUIDE}
标题列表：
{numbered}
"""
    raw = chat_completion(
        prompt,
        system_prompt="你是 A 股公告事件分类器。严格按枚举输出 JSON，不要解释。",
        max_tokens=800,
        temperature=0.0,
        json_mode=True,
        max_retries=1,
    )
    return _parse_llm_batch(raw, len(titles))


def _title_key(title: str) -> str:
    return str(title or "").strip()[:200]


def _unclassified_rows(
    conn: sqlite3.Connection,
    table: str,
    stock_id: int,
    *,
    limit: int,
) -> list[tuple[int, str]]:
    if table == "stock_announcements":
        return conn.execute(
            """SELECT id, title FROM stock_announcements
               WHERE stock_id=? AND (event_type IS NULL OR event_type='')
               ORDER BY pub_date DESC LIMIT ?""",
            (stock_id, limit),
        ).fetchall()
    if not conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='stock_news'"
    ).fetchone():
        return []
    return conn.execute(
        """SELECT id, title FROM stock_news
           WHERE stock_id=? AND (event_type IS NULL OR event_type='')
           ORDER BY pub_date DESC LIMIT ?""",
        (stock_id, limit),
    ).fetchall()


def _collect_pending_rows(
    conn: sqlite3.Connection,
    table: str,
    stock_ids: list[int] | None,
    *,
    limit_per_stock: int,
) -> tuple[list[tuple[int, str]], dict[str, list[int]]]:
    """收集待分类行，按标题去重（同标题多行只调一次 LLM）。"""
    if stock_ids:
        ph = ",".join("?" * len(stock_ids))
        stocks = conn.execute(
            f"SELECT id FROM stocks WHERE id IN ({ph}) AND is_active=1",
            stock_ids,
        ).fetchall()
    else:
        stocks = conn.execute("SELECT id FROM stocks WHERE is_active=1").fetchall()

    rows: list[tuple[int, str]] = []
    title_to_ids: dict[str, list[int]] = {}
    for (sid,) in stocks:
        for row_id, title in _unclassified_rows(
            conn, table, int(sid), limit=limit_per_stock
        ):
            key = _title_key(title)
            if not key:
                continue
            rows.append((int(row_id), key))
            title_to_ids.setdefault(key, []).append(int(row_id))
    return rows, title_to_ids


def _resolve_title_types(
    unique_titles: list[str],
    *,
    batch_size: int,
    concurrency: int,
    sleep_s: float,
    use_cache: bool = True,
    conn: sqlite3.Connection | None = None,
) -> tuple[dict[str, str], int, int, list[str]]:
    """查缓存 + 并发 LLM，返回 title -> event_type（仅非空）。"""
    if not unique_titles:
        return {}, 0, 0, []

    from services.event_title_cache import lookup_titles, store_titles

    title_types: dict[str, str] = {}
    cache_hits = 0
    pending = list(unique_titles)

    if use_cache:
        cached = lookup_titles(pending, conn=conn)
        cache_hits = len(cached)
        pending = [t for t in pending if t not in cached]
        for title, et in cached.items():
            if et:
                title_types[title] = et

    if not pending:
        return title_types, 0, cache_hits, []

    batches: list[list[str]] = [
        pending[i : i + batch_size] for i in range(0, len(pending), batch_size)
    ]
    errors: list[str] = []
    workers = max(1, min(concurrency, len(batches)))
    llm_results: dict[str, str] = {}

    def _run_batch(batch: list[str]) -> tuple[list[str], list[str]]:
        if sleep_s > 0:
            time.sleep(sleep_s)
        return batch, classify_titles_llm(batch)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_run_batch, batch): batch for batch in batches}
        for fut in as_completed(futures):
            batch = futures[fut]
            try:
                titles, types = fut.result()
            except Exception as e:
                errors.append(str(e))
                continue
            for title, et in zip(titles, types):
                llm_results[title] = et or ""
                if et:
                    title_types[title] = et

    if use_cache and llm_results:
        store_titles(llm_results, conn=conn)

    return title_types, len(batches), cache_hits, errors[:5]


def _apply_llm_to_table(
    conn: sqlite3.Connection,
    table: str,
    stock_ids: list[int] | None,
    *,
    limit_per_stock: int,
    batch_size: int,
    concurrency: int,
    sleep_s: float,
) -> dict[str, Any]:
    id_col = "id"
    rows, title_to_ids = _collect_pending_rows(
        conn, table, stock_ids, limit_per_stock=limit_per_stock
    )
    if not rows:
        return {
            "table": table,
            "scanned": 0,
            "classified": 0,
            "batches": 0,
            "cache_hits": 0,
            "unique_titles": 0,
            "errors": [],
        }

    from services.event_title_cache import ensure_table

    ensure_table(conn)

    unique_titles = list(title_to_ids.keys())
    title_types, batches, cache_hits, errors = _resolve_title_types(
        unique_titles,
        batch_size=batch_size,
        concurrency=concurrency,
        sleep_s=sleep_s,
        conn=conn,
    )

    classified = 0
    for title, row_ids in title_to_ids.items():
        et = title_types.get(title)
        if not et:
            continue
        for row_id in row_ids:
            conn.execute(
                f"UPDATE {table} SET event_type=? WHERE {id_col}=?",
                (et, row_id),
            )
            classified += 1

    return {
        "table": table,
        "scanned": len(rows),
        "classified": classified,
        "batches": batches,
        "cache_hits": cache_hits,
        "unique_titles": len(unique_titles),
        "errors": errors,
    }


def classify_events_llm(
    stock_ids: list[int] | None = None,
    *,
    limit_per_stock: int = 12,
    batch_size: int | None = None,
    concurrency: int | None = None,
    sleep_s: float | None = None,
) -> dict[str, Any]:
    """对规则未命中的公告/新闻做 LLM 补充分类（全局批处理 + 标题去重 + 并发）。"""
    if not is_llm_available():
        return {"skipped": True, "reason": "LLM 未配置"}

    batch_size = batch_size if batch_size is not None else config.EVENT_LLM_BATCH_SIZE
    concurrency = (
        concurrency if concurrency is not None else config.EVENT_LLM_CONCURRENCY
    )
    sleep_s = (
        sleep_s
        if sleep_s is not None
        else config.EVENT_LLM_SLEEP_MS / 1000.0
    )

    conn = sqlite3.connect(config.DB_PATH)
    try:
        from services.event_classifier import _ensure_event_type_column

        _ensure_event_type_column(conn, "stock_announcements")
        ann = _apply_llm_to_table(
            conn,
            "stock_announcements",
            stock_ids,
            limit_per_stock=limit_per_stock,
            batch_size=batch_size,
            concurrency=concurrency,
            sleep_s=sleep_s,
        )
        news: dict[str, Any] = {"skipped": True, "reason": "stock_news missing"}
        if conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='stock_news'"
        ).fetchone():
            _ensure_event_type_column(conn, "stock_news")
            news = _apply_llm_to_table(
                conn,
                "stock_news",
                stock_ids,
                limit_per_stock=limit_per_stock,
                batch_size=batch_size,
                concurrency=concurrency,
                sleep_s=sleep_s,
            )
        conn.commit()
        return {
            "announcements": ann,
            "news": news,
            "classified_total": ann.get("classified", 0) + news.get("classified", 0),
            "batch_size": batch_size,
            "concurrency": concurrency,
        }
    finally:
        conn.close()
