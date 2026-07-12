"""上市公司公告 — 东财 np-anotice + 巨潮 cninfo fallback。"""
from __future__ import annotations

import sqlite3
from typing import Optional

from config import DB_PATH
from services.data_processor import normalize_code
from services.http_client import get, post


def ensure_announcements_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS stock_announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL REFERENCES stocks(id),
            title TEXT NOT NULL,
            ann_type TEXT DEFAULT '',
            pub_date TEXT NOT NULL,
            url TEXT DEFAULT '',
            pdf_url TEXT DEFAULT '',
            source TEXT DEFAULT 'eastmoney',
            art_code TEXT DEFAULT '',
            UNIQUE(stock_id, art_code)
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ann_stock_date ON stock_announcements(stock_id, pub_date DESC)"
    )


def _cninfo_column(code: str) -> str:
    code = normalize_code(code)
    if code.startswith(("8", "4")):
        return "bj"
    if code.startswith("6"):
        return "sse"
    return "szse"


def fetch_announcements_eastmoney(code: str, limit: int = 30) -> list[dict]:
    code = normalize_code(code)
    url = "https://np-anotice-stock.eastmoney.com/api/security/ann"
    params = {
        "sr": "-1",
        "page_size": str(min(limit, 50)),
        "page_index": "1",
        "ann_type": "A",
        "client_source": "web",
        "stock_list": code,
    }
    r = get(url, params=params, timeout=15)
    data = r.json().get("data") or {}
    items = data.get("list") or []
    out: list[dict] = []
    for item in items[:limit]:
        art_code = str(item.get("art_code") or "")
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        pub_date = str(item.get("notice_date") or "")[:10]
        ann_type = ""
        cols = item.get("columns") or []
        if cols:
            ann_type = str(cols[0].get("column_name") or "")
        detail_url = f"https://data.eastmoney.com/notices/detail/{code}/{art_code}.html"
        out.append(
            {
                "title": title,
                "ann_type": ann_type,
                "pub_date": pub_date,
                "url": detail_url,
                "pdf_url": "",
                "source": "eastmoney",
                "art_code": art_code,
            }
        )
    return out


def fetch_announcements_cninfo(code: str, limit: int = 30) -> list[dict]:
    code = normalize_code(code)
    url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Referer": "https://www.cninfo.com.cn/",
        "X-Requested-With": "XMLHttpRequest",
    }
    data = {
        "stock": f"{code},",
        "tabName": "fulltext",
        "pageSize": str(min(limit, 30)),
        "pageNum": "1",
        "column": _cninfo_column(code),
        "category": "",
        "plate": "",
        "seDate": "",
        "searchkey": "",
        "secid": "",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    r = post(url, data=data, headers=headers, timeout=15)
    anns = r.json().get("announcements") or []
    out: list[dict] = []
    for a in anns[:limit]:
        title = str(a.get("announcementTitle") or "").strip()
        if not title:
            continue
        ts = a.get("announcementTime")
        pub_date = ""
        if ts:
            try:
                from datetime import datetime

                pub_date = datetime.fromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d")
            except Exception:
                pub_date = str(ts)[:10]
        adjunct = str(a.get("adjunctUrl") or "")
        pdf_url = f"https://static.cninfo.com.cn/{adjunct}" if adjunct else ""
        ann_id = str(a.get("announcementId") or adjunct or title)
        out.append(
            {
                "title": title,
                "ann_type": str(a.get("announcementType") or ""),
                "pub_date": pub_date,
                "url": pdf_url or f"https://www.cninfo.com.cn/new/disclosure/detail?stockCode={code}",
                "pdf_url": pdf_url,
                "source": "cninfo",
                "art_code": ann_id,
            }
        )
    return out


def fetch_announcements_for_stock(code: str, limit: int = 30) -> list[dict]:
    rows = fetch_announcements_eastmoney(code, limit=limit)
    if len(rows) >= min(5, limit):
        return rows
    seen = {r["art_code"] for r in rows if r.get("art_code")}
    for r in fetch_announcements_cninfo(code, limit=limit):
        if r.get("art_code") not in seen:
            rows.append(r)
            seen.add(r.get("art_code"))
        if len(rows) >= limit:
            break
    return rows[:limit]


def sync_announcements(
    stock_id: int,
    code: str,
    *,
    limit: int = 30,
    conn: Optional[sqlite3.Connection] = None,
) -> int:
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    ensure_announcements_table(conn)
    articles = fetch_announcements_for_stock(code, limit=limit)
    added = 0
    for a in articles:
        art_code = a.get("art_code") or a["title"][:40]
        cur = conn.execute(
            """INSERT OR IGNORE INTO stock_announcements
               (stock_id, title, ann_type, pub_date, url, pdf_url, source, art_code)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                stock_id,
                a["title"][:300],
                a.get("ann_type", ""),
                a.get("pub_date", ""),
                a.get("url", ""),
                a.get("pdf_url", ""),
                a.get("source", "eastmoney"),
                art_code,
            ),
        )
        if cur.rowcount:
            added += 1
    if own:
        conn.commit()
        conn.close()
    return added


def sync_all_announcements(
    stock_ids: list[int] | None = None,
    *,
    limit: int = 30,
    sleep_s: float = 0.2,
) -> dict:
    """全市场/指定股票批量抓取公告入库。"""
    import time

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_announcements_table(conn)
    try:
        if stock_ids:
            ph = ",".join("?" * len(stock_ids))
            rows = conn.execute(
                f"""SELECT id, code FROM stocks
                    WHERE id IN ({ph}) AND is_active=1 ORDER BY id""",
                stock_ids,
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, code FROM stocks WHERE is_active=1 ORDER BY id"
            ).fetchall()
    finally:
        pass

    added = 0
    errors: list[str] = []
    for row in rows:
        sid, code = int(row["id"]), row["code"]
        try:
            added += sync_announcements(sid, code, limit=limit, conn=conn)
        except Exception as e:
            errors.append(f"{code}:{e}")
        time.sleep(sleep_s)

    conn.commit()
    conn.close()
    return {
        "stocks": len(rows),
        "added": added,
        "limit_per_stock": limit,
        "errors": errors[:10],
    }


def get_announcements_from_db(stock_id: int, limit: int = 30) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT title, ann_type, pub_date, url, pdf_url, source
           FROM stock_announcements WHERE stock_id=?
           ORDER BY pub_date DESC LIMIT ?""",
        (stock_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
