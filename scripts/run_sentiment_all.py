"""批量执行全量情绪面分析，同步等待完成，结果写入 sentiment_scores + comprehensive_scores"""
import sys
import os
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import sqlite3
from config import DB_PATH
from services.comprehensive_store import upsert_dimension_score, resolve_calc_date


def execute_sql(query, params=None):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(query, params or ())
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def main():
    stocks = execute_sql("SELECT id, code, name FROM stocks WHERE is_active=1")
    print(f"共 {len(stocks)} 只活跃股票，开始分析...\n")

    from services.sentiment_scorer import compute_sentiment_score

    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS sentiment_scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT, stock_id INTEGER NOT NULL,
        date TEXT NOT NULL, composite_score REAL, turnover_score REAL,
        leverage_score REAL, limit_score REAL, rsi_score REAL,
        breakdown_json TEXT, UNIQUE(stock_id, date))""")
    conn.commit()

    success = 0
    fail = 0
    results = []
    start_time = time.time()

    for i, s in enumerate(stocks):
        try:
            r = compute_sentiment_score(s["id"], s["code"])
            if "error" in r:
                print(f"[{i+1}/{len(stocks)}] {s['code']} {s['name']} ERR: {r['error']}")
                fail += 1
                continue

            score = r.get("composite_score", r.get("score", 0))
            calc_date = resolve_calc_date(conn, s["id"])
            conn.execute(
                """INSERT OR REPLACE INTO sentiment_scores
                (stock_id,date,composite_score,turnover_score,leverage_score,limit_score,rsi_score,breakdown_json)
                VALUES (?,?,?,?,?,?,?,?)""",
                (
                    s["id"],
                    r.get("date"),
                    score,
                    r.get("turnover_score", r.get("turn_score", 0)),
                    r.get("leverage_score", 0),
                    r.get("limit_score", 0),
                    r.get("rsi_score", 0),
                    json.dumps(r.get("signals", {})),
                ),
            )
            conn.commit()
            upsert_dimension_score(s["id"], "mood_score", float(score), calc_date=calc_date)

            results.append(r)
            success += 1
            print(f"[{i+1}/{len(stocks)}] {s['code']} {s['name']} 综合={score}")

            time.sleep(0.3)
        except Exception as e:
            print(f"[{i+1}/{len(stocks)}] {s['code']} {s['name']} EXCEPTION: {e}")
            fail += 1

    conn.close()
    elapsed = time.time() - start_time

    if results:
        scores = [r.get("composite_score", r.get("score", 0)) for r in results]
        print(f"\n{'='*60}")
        print(f"全量情绪面分析完成 | 耗时 {elapsed:.0f}s")
        print(f"成功: {success} | 失败: {fail}")
        print(f"综合评分: 均分={sum(scores)/len(scores):.1f} 最低={min(scores)} 最高={max(scores)}")
        print(f"{'='*60}")

    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
