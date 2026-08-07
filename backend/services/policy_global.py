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


def _parse_llm_json(text: str) -> dict:
    """宽松解析 LLM 返回的 JSON：常规解析失败(多为被截断)时,挽救到最后一个完整对象。"""
    start = text.find("{")
    end = text.rfind("}") + 1
    if not (0 <= start < end):
        raise ValueError("响应中无 JSON")
    frag = text[start:end]
    try:
        return json.loads(frag)
    except json.JSONDecodeError:
        depth = 0
        last_ok = -1
        for i, ch in enumerate(frag):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    last_ok = i + 1
        if last_ok > 0:
            return json.loads(frag[:last_ok])
        raise


_POLICY_JSON_SCHEMA = """返回纯JSON：
{
  "industries": {
    "行业名": {
      "tendency": "强力支持/支持/中性/限制/强力限制",
      "score": 0-100 (强力支持>80, 支持60-80, 中性40-60, 限制20-40, 强力限制<20),
      "summary": "20字以内政策判断",
      "reasons": ["关键政策1", "关键政策2"]
    }
  },
  "macro": "宏观政策环境20字总结"
}"""


def _scan_policy_batch(batch: list, news_by_industry: dict, today: str) -> dict:
    """扫描一批行业：独立 LLM 调用(json_mode + 重试)+ 容错解析。失败返回 {'error':...}。"""
    sections = []
    for ind in batch:
        titles = news_by_industry.get(ind, [])
        titles_text = "\n".join(f"  - {t}" for t in titles[:10])
        sections.append(f"## {ind}\n{titles_text if titles_text else '  （无近期新闻）'}")
    prompt = (
        f"你是中国产业政策分析专家。分析以下 {len(batch)} 个行业的近期政策环境和新闻，"
        f"只覆盖这些行业，按标准格式输出。\n\n"
        f"{chr(10).join(sections)}\n\n{_POLICY_JSON_SCHEMA}"
    )
    try:
        from services.llm_client import chat_completion

        text = chat_completion(
            prompt,
            system_prompt="你是中国产业政策分析专家，输出纯JSON。",
            max_tokens=2500,
            temperature=0.1,
            json_mode=True,
            max_retries=2,
        )
        return _parse_llm_json(text)
    except Exception as e:  # noqa: BLE001 - LLM/解析异常统一降级为该批失败
        return {"error": str(e)[:120]}


def _persist_policy_snapshot(today: str, industries_data: dict) -> int:
    """写入一批行业政策到 policy_snapshot（每批独立提交，部分成功也保留）。"""
    if not industries_data:
        return 0
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
        wconn.execute(
            """CREATE TABLE IF NOT EXISTS policy_snapshot (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL, industry TEXT NOT NULL,
                tendency TEXT, score REAL, summary TEXT, reasons_json TEXT,
                UNIQUE(date, industry))"""
        )
        n = 0
        for ind, data in industries_data.items():
            if not isinstance(data, dict):
                continue
            wconn.execute(
                """INSERT OR REPLACE INTO policy_snapshot
                    (date, industry, tendency, score, summary, reasons_json)
                    VALUES (?,?,?,?,?,?)""",
                (today, ind, data.get("tendency"), data.get("score"),
                 data.get("summary"),
                 json.dumps(data.get("reasons", []), ensure_ascii=False)),
            )
            n += 1
        wconn.commit()
        return n
    finally:
        if has_lock:
            write_lock.release()
        else:
            wconn.close()


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

    # 分批扫描：31 个申万一级行业切成每批 ~12 个，逐批调 LLM、独立解析、独立入库。
    # 原来一次性全塞 + max_tokens=1500 → 输出 JSON 被截断 → 整批失败 → 快照冻结数月。
    BATCH = 12
    inds_with_data = list(news_by_industry.keys())
    all_industries: dict = {}
    macro_parts: list = []
    errors: list = []

    for bi in range(0, len(inds_with_data), BATCH):
        batch = inds_with_data[bi:bi + BATCH]
        parsed = _scan_policy_batch(batch, news_by_industry, today)
        if parsed.get("error"):
            errors.append(f"批{bi // BATCH + 1}: {parsed['error']}")
            continue
        industries_data = parsed.get("industries", {}) or {}
        all_industries.update(industries_data)
        if parsed.get("macro"):
            macro_parts.append(str(parsed["macro"]))
        _persist_policy_snapshot(today, industries_data)  # 每批入库，部分成功也保留

    if not all_industries:
        return {"error": "全部批次失败: " + "; ".join(errors)[:200]}

    return {
        "status": "done",
        "industries_scanned": len(all_industries),
        "batches_failed": len(errors),
        "errors": errors,
        "macro": " | ".join(macro_parts),
        "snapshot": all_industries,
    }


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
