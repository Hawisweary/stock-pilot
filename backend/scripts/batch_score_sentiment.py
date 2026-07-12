"""并行关键词情感打分 — 按股票新闻池并发处理，批量写回。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import config
from services.news_fetcher import score_text_keywords


def _load_unscored() -> dict[int, list[tuple[int, str]]]:
    """读所有未评分新闻，按 stock_id 分组为 {sid: [(news_id, text), ...]}"""
    conn = sqlite3.connect(config.DB_PATH)
    rows = conn.execute(
        "SELECT id, stock_id, title, content FROM stock_news WHERE sentiment_score IS NULL"
    ).fetchall()
    conn.close()
    pool: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for nid, sid, title, content in rows:
        text = f"{title or ''} {content or ''}".strip()
        pool[int(sid)].append((int(nid), text))
    return pool


def _score_pool(sid: int, items: list[tuple[int, str]]) -> list[tuple[int, float, str]]:
    """对单只股票的新闻池打分，返回 [(news_id, score, label), ...]"""
    results = []
    for nid, text in items:
        score, label = score_text_keywords(text)
        results.append((nid, score, label))
    return results


def _write_batch(scored: list[tuple[int, float, str]]) -> int:
    if not scored:
        return 0
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executemany(
        "UPDATE stock_news SET sentiment_score=?, sentiment_label=? WHERE id=?",
        [(score, label, nid) for nid, score, label in scored],
    )
    conn.commit()
    conn.close()
    return len(scored)


def main(workers: int = 16):
    print("加载未评分新闻...")
    pool = _load_unscored()
    total_stocks = len(pool)
    total_news = sum(len(v) for v in pool.values())
    print(f"待处理: {total_stocks} 只股票, {total_news} 条新闻")

    all_scored: list[tuple[int, float, str]] = []
    done = 0

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_score_pool, sid, items): sid for sid, items in pool.items()}
        for fut in as_completed(futures):
            results = fut.result()
            all_scored.extend(results)
            done += 1
            if done % 500 == 0 or done == total_stocks:
                print(f"  打分进度: {done}/{total_stocks} 只 ({len(all_scored)} 条)")

    print(f"打分完成，共 {len(all_scored)} 条，写回数据库...")
    # 分批写入避免单事务过大
    CHUNK = 5000
    written = 0
    for i in range(0, len(all_scored), CHUNK):
        written += _write_batch(all_scored[i:i + CHUNK])
        print(f"  写入: {written}/{len(all_scored)}")

    print("更新 comprehensive_scores sentiment_score...")
    import database
    database.init()
    from services.batch_score_compute import compute_sentiment_news
    from config import latest_trading_date
    r = compute_sentiment_news(None, latest_trading_date())
    print(f"  sentiment synced={r.get('synced')}, skipped={r.get('skipped_null')}")

    print("重算 V5...")
    from services.v5_scorer import compute_all_v5_scores
    v5 = compute_all_v5_scores()
    print(f"  V5 computed={v5.get('computed')}")

    print("全部完成！")


if __name__ == "__main__":
    main()
