"""政策面全局扫描引擎 — 每天1次 LLM扫描 → 行业映射"""
import sqlite3, json, re, time
from datetime import date

from config import DB_PATH


def _connect_db():
    """连接数据库，带超时等待 + 重试（避免 WAL 模式下锁冲突）"""
    for attempt in range(5):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA journal_mode=WAL")
            return conn
        except sqlite3.OperationalError as e:
            if "locked" in str(e) and attempt < 4:
                time.sleep(2 * (attempt + 1))
            else:
                raise


def _get_db():
    """优先使用后端全局连接（避免锁冲突），不可用时回退到独立连接"""
    try:
        from database import get as get_db
        return get_db()
    except RuntimeError:
        return _connect_db()


def global_policy_scan() -> dict:
    """
    全局政策扫描：
    1. 收集所有活跃行业的近期新闻标题
    2. LLM 一次性输出「行业→政策倾向」映射表
    3. 存入 policy_snapshot 表
    4. 个股直接查表获取政策分（零 LLM 调用）
    """
    today = date.today().strftime("%Y-%m-%d")

    # 使用后端全局连接读取（避免锁冲突）
    try:
        from database import get as get_db
        conn = get_db()
        is_global_conn = True
    except RuntimeError:
        conn = _connect_db()
        conn.row_factory = sqlite3.Row
        is_global_conn = False

    # 获取所有行业列表
    industries = conn.execute(
        "SELECT DISTINCT industry_sw FROM stocks WHERE is_active=1 AND industry_sw IS NOT NULL"
    ).fetchall()
    industry_list = [r["industry_sw"] for r in industries]

    # 收集每个行业的代表性新闻（取最近30条新闻标题）
    news_by_industry = {}
    for ind in industry_list:
        stocks = conn.execute(
            "SELECT id FROM stocks WHERE industry_sw=? AND is_active=1 LIMIT 5",
            (ind,)
        ).fetchall()
        sids = [s["id"] for s in stocks]
        if not sids:
            continue
        placeholders = ",".join("?" for _ in sids)
        news_rows = conn.execute(
            f"SELECT title FROM stock_news WHERE stock_id IN ({placeholders}) "
            f"AND pub_date >= DATE('now','-7 days') ORDER BY pub_date DESC LIMIT 30",
            sids
        ).fetchall()
        news_by_industry[ind] = [r["title"][:60] for r in news_rows]

    # 全局连接不关闭
    if not is_global_conn:
        conn.close()

    # 构建 LLM prompt
    sections = []
    for ind, titles in news_by_industry.items():
        titles_text = "\n".join(f"  - {t}" for t in titles[:10])
        sections.append(f"## {ind}\n{titles_text if titles_text else '  （无近期新闻）'}")

    prompt = f"""你是中国产业政策分析专家。分析以下各行业的近期政策环境和新闻，按标准格式输出。

{chr(10).join(sections)}

返回纯JSON（不要markdown标记）：
{{
  "date": "{today}",
  "industries": {{
    "行业名": {{
      "tendency": "强力支持/支持/中性/限制/强力限制",
      "score": 0-100 (强力支持>80, 支持60-80, 中性40-60, 限制20-40, 强力限制<20),
      "summary": "20字以内政策判断",
      "reasons": ["关键政策1", "关键政策2"]
    }}
  }},
  "macro": "宏观政策环境20字总结"
}}"""

    try:
        from services.news_fetcher import chat_completion
        text = chat_completion(prompt, system_prompt="你是中国产业政策分析专家，输出纯JSON。",
                              max_tokens=1500, temperature=0.1)
        start = text.find("{")
        end = text.rfind("}") + 1
        if 0 <= start < end:
            result = json.loads(text[start:end])
        else:
            return {"error": "LLM 返回非 JSON", "raw": text[:100]}
    except Exception as e:
        return {"error": f"LLM 调用失败: {e}"}

    # 存入 snapshot 表（使用 write_lock 避免并发冲突）
    try:
        from database import get as get_db, write_lock
        wconn = get_db()
        has_lock = True
    except RuntimeError:
        wconn = _connect_db()
        has_lock = False

    if has_lock:
        write_lock.acquire()

    try:
        wconn.execute("""CREATE TABLE IF NOT EXISTS policy_snapshot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL, industry TEXT NOT NULL,
            tendency TEXT, score REAL, summary TEXT, reasons_json TEXT,
            UNIQUE(date, industry))""")

        count = 0
        industries_data = result.get("industries", {})
        for ind, data in industries_data.items():
            wconn.execute("""INSERT OR REPLACE INTO policy_snapshot
                (date, industry, tendency, score, summary, reasons_json)
                VALUES (?,?,?,?,?,?)""",
                (today, ind, data.get("tendency"), data.get("score"),
                 data.get("summary"), json.dumps(data.get("reasons", []), ensure_ascii=False)))
            count += 1

        wconn.commit()
    finally:
        if has_lock:
            write_lock.release()

    return {"status": "done", "industries_scanned": count, "macro": result.get("macro", ""),
            "snapshot": industries_data}


def get_policy_for_stock(stock_id: int) -> dict | None:
    """从全局快照查个股政策分（零 LLM 调用）"""
    today = date.today().strftime("%Y-%m-%d")
    try:
        from database import get as get_db
        conn = get_db()
        is_global_conn = True
    except RuntimeError:
        conn = _connect_db()
        conn.row_factory = sqlite3.Row
        is_global_conn = False

    try:
        row = conn.execute(
            """SELECT ps.* FROM stocks s
            JOIN policy_snapshot ps ON ps.industry=s.industry_sw AND ps.date=?
            WHERE s.id=?""", (today, stock_id)).fetchone()

        # 如果没有今日快照，取最新
        if not row:
            row = conn.execute(
                """SELECT ps.* FROM stocks s
                JOIN policy_snapshot ps ON ps.industry=s.industry_sw
                WHERE s.id=? ORDER BY ps.date DESC LIMIT 1""", (stock_id,)).fetchone()
    finally:
        if not is_global_conn:
            conn.close()

    if not row:
        return None

    result = dict(row)
    if result.get("reasons_json"):
        result["reasons"] = json.loads(result["reasons_json"])
    return result
