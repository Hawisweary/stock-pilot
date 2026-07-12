#!/usr/bin/env python3
"""全局政策面扫描 — 独立脚本，绕过 uvicorn 的 SQLite 锁"""
import sys, os, json, time

# 设置路径和加载环境变量
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
backend_dir = os.path.join(PROJECT_DIR, 'backend')
sys.path.insert(0, backend_dir)

# 加载 .env
env_file = os.path.join(PROJECT_DIR, '.env')
if os.path.exists(env_file):
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, _, val = line.partition('=')
                os.environ[key.strip()] = val.strip()

from datetime import date
today = date.today().strftime('%Y-%m-%d')
DB = os.path.join(PROJECT_DIR, 'data', 'afr.db')

import sqlite3

print(f"=== 全局政策扫描 {today} ===")
print(f"DB: {DB}")

# 1. 读取行业和新闻
conn = sqlite3.connect(DB, timeout=120)
conn.execute('PRAGMA busy_timeout=120000')
conn.execute('PRAGMA journal_mode=WAL')
conn.row_factory = sqlite3.Row

industries = conn.execute(
    'SELECT DISTINCT industry_sw FROM stocks WHERE is_active=1 AND industry_sw IS NOT NULL'
).fetchall()
industry_list = [r['industry_sw'] for r in industries]
print(f"行业数: {len(industries)}")

news_by_industry = {}
for ind in industry_list:
    stocks = conn.execute('SELECT id FROM stocks WHERE industry_sw=? AND is_active=1 LIMIT 5', (ind,)).fetchall()
    sids = [s['id'] for s in stocks]
    if not sids:
        continue
    ph = ','.join('?' for _ in sids)
    rows = conn.execute(
        f"SELECT title FROM stock_news WHERE stock_id IN ({ph}) "
        f"AND pub_date >= DATE('now','-7 days') ORDER BY pub_date DESC LIMIT 30", sids
    ).fetchall()
    news_by_industry[ind] = [r['title'][:60] for r in rows]
    print(f"  {ind}: {len(rows)} 条新闻")
conn.close()

# 2. 构建 prompt
sections = []
for ind, titles in news_by_industry.items():
    t = '\n'.join(f"  - {x}" for x in titles[:10])
    sections.append(f"## {ind}\n{t if t else '  (无近期新闻)'}")

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

# 3. 调用 LLM
print("\n调用 LLM (DeepSeek V4-Pro)...")
from services.llm_client import chat_completion
t0 = time.time()
text = chat_completion(prompt, system_prompt="你是中国产业政策分析专家，输出纯JSON。",
                       max_tokens=1500, temperature=0.1)
llm_time = time.time() - t0
print(f"LLM 耗时: {llm_time:.1f}s, 返回长度: {len(text)} 字符")

start = text.find("{")
end = text.rfind("}") + 1
if 0 <= start < end:
    result = json.loads(text[start:end])
else:
    print(f"ERROR: LLM 返回非 JSON: {text[:200]}")
    sys.exit(1)

ind_data = result.get("industries", {})

# 4. 写入 policy_snapshot (BEGIN IMMEDIATE + 重试)
def write_with_retry(write_fn, label, max_retries=8):
    for attempt in range(max_retries):
        try:
            return write_fn()
        except sqlite3.OperationalError as e:
            print(f"  {label} 重试 {attempt+1}/{max_retries}: {e}")
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"{label} 写入失败: {max_retries} 次重试后仍被锁")

def write_snapshot():
    c = sqlite3.connect(DB, timeout=120)
    c.execute('PRAGMA busy_timeout=120000')
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('BEGIN IMMEDIATE')
    c.execute("""CREATE TABLE IF NOT EXISTS policy_snapshot (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL, industry TEXT NOT NULL,
        tendency TEXT, score REAL, summary TEXT, reasons_json TEXT,
        UNIQUE(date, industry))""")
    cnt = 0
    for ind, d in ind_data.items():
        c.execute("""INSERT OR REPLACE INTO policy_snapshot
            (date, industry, tendency, score, summary, reasons_json)
            VALUES (?,?,?,?,?,?)""",
            (today, ind, d.get("tendency"), d.get("score"),
             d.get("summary"), json.dumps(d.get("reasons", []), ensure_ascii=False)))
        cnt += 1
    c.commit()
    c.close()
    return cnt

print("写入 policy_snapshot...")
count = write_with_retry(write_snapshot, "snapshot")
print(f"写入 {count} 个行业快照")

# 5. 写入 policy_scores
def write_scores():
    c = sqlite3.connect(DB, timeout=120)
    c.execute('PRAGMA busy_timeout=120000')
    c.execute('PRAGMA journal_mode=WAL')
    c.row_factory = sqlite3.Row
    c.execute('BEGIN IMMEDIATE')
    c.execute("""CREATE TABLE IF NOT EXISTS policy_scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stock_id INTEGER NOT NULL, date TEXT NOT NULL,
        composite_score REAL, breakdown_json TEXT,
        UNIQUE(stock_id, date))""")
    stocks = c.execute('SELECT id, code FROM stocks WHERE is_active=1').fetchall()
    upd = 0
    for s in stocks:
        row = c.execute(
            """SELECT ps.* FROM stocks s
            JOIN policy_snapshot ps ON ps.industry=s.industry_sw AND ps.date=?
            WHERE s.id=?""", (today, s["id"])).fetchone()
        if row:
            c.execute("""INSERT OR REPLACE INTO policy_scores
                (stock_id, date, composite_score, breakdown_json)
                VALUES (?,?,?,?)""",
                (s["id"], today, row["score"],
                 json.dumps({"source": "global_scan", "tendency": row["tendency"]}, ensure_ascii=False)))
            try:
                c.execute("""INSERT OR REPLACE INTO dimension_scores
                    (stock_id, dimension, score, updated_at)
                    VALUES (?, 'policy_score', ?, datetime('now'))""",
                    (s["id"], row["score"]))
            except Exception:
                pass
            upd += 1
    c.commit()
    c.close()
    return upd

print("写入 policy_scores...")
updated = write_with_retry(write_scores, "scores")
print(f"更新 {updated} 只股票")

# 6. 输出结果
macro = result.get("macro", "")
print(f"\n{'='*50}")
print(f"全局政策面扫描完成 — {today}")
print(f"{'='*50}")
print(f"LLM: DeepSeek V4-Pro, 1 次调用, 耗时 {llm_time:.1f}s")
print(f"覆盖行业: {count}")
print(f"更新股票: {updated}")
print(f"宏观判断: {macro}")
print(f"\n--- 行业评分（由高到低）---")
for ind, d in sorted(ind_data.items(), key=lambda x: x[1].get("score", 0), reverse=True):
    s = d.get("score", 0)
    t = d.get("tendency", "中性")
    sm = d.get("summary", "")
    rs = d.get("reasons", [])
    print(f"  {ind}: {s} ({t}) — {sm}")
    for r in rs[:2]:
        print(f"    · {r}")

# 保存 JSON 结果
output = {
    "date": today, "llm_time": round(llm_time, 1),
    "industries_scanned": count, "stocks_updated": updated,
    "macro": macro, "industries": ind_data
}
result_file = os.path.join(PROJECT_DIR, 'data', 'policy_scan_result.json')
with open(result_file, 'w') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)
print(f"\n结果已保存: {result_file}")
