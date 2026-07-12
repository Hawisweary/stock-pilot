"""批量补齐 stocks.industry_sw — 多线程并发

背景: 全市场只有 8.8%（465/5292）股票有申万行业分类标签。回填逻辑
services/industry_backfill.py::backfill_missing_industries() 本身没问题，
但是纯串行 for 循环，对每只缺标签的股票单独发一次 adata 网络请求，全市场
规模下从未真正跑完过。

跟行情同步不同，这里每只股票只有"一次网络查询 + 一次 UPDATE"，不会像
batch_sync_quotes.py 那样撞上 SQLite 写锁问题。但底层数据源（adata → 爬
百度股市通）本身不稳定，同一代码连续调用可能一次有一次空，脚本内置了
3 次短退避重试；并发度故意调低（默认6），并发太高更容易被限流打空。

由于是幂等的（只处理 industry_sw 为空的行），如果第一轮跑完还有 fail，
直接重复运行几次脚本即可继续补剩下的。

依赖注意: adata 库只装在 /usr/bin/python3（3.9）环境，不在 /usr/local/bin/python3
（3.14）里，必须用前者运行，否则 adata 查询会静默返回空结果。

用法:
    python3 scripts/batch_backfill_industries.py                 # 只补缺失的（用 /usr/bin/python3）
    python3 scripts/batch_backfill_industries.py --workers 10     # 指定并发（默认6）
    python3 scripts/batch_backfill_industries.py --limit 200      # 只处理前N只（测试用）
    python3 scripts/batch_backfill_industries.py --normalize      # 补完后顺带跑一次归一化
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

DB_PATH = config.DB_PATH
_print_lock = Lock()
_counter_lock = Lock()
_stats = {"ok": 0, "fail": 0, "total": 0}


def _log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def _inc(key: str) -> None:
    with _counter_lock:
        _stats[key] += 1


def _fetch_one(stock_id: int, code: str, name: str, existing_industry: str) -> dict:
    """单股行业分类补齐 — 每个线程独立连接。

    底层数据源（adata → 百度股市通爬取）不稳定，同一代码连续调用可能一次有
    一次空，所以这里做几次短退避重试，而不是一次空就放弃。
    """
    from services.industry_backfill import MANUAL_INDUSTRY_SW, _fetch_industry
    from services.industry_normalize import normalize_industry

    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
    conn.row_factory = sqlite3.Row
    try:
        raw = existing_industry or ""
        if code in MANUAL_INDUSTRY_SW:
            sw = MANUAL_INDUSTRY_SW[code]
        else:
            if not raw:
                for attempt in range(3):
                    raw = _fetch_industry(code) or ""
                    if raw:
                        break
                    time.sleep(0.5 * (attempt + 1))
            sw = normalize_industry(raw, conn) if raw else ""
        if not sw:
            return {"stock_id": stock_id, "code": code, "sw": None}

        raw_to_store = raw or sw
        conn.execute(
            "UPDATE stocks SET industry=COALESCE(NULLIF(industry,''), ?), industry_sw=? WHERE id=?",
            (raw_to_store, sw, stock_id),
        )
        conn.commit()
        return {"stock_id": stock_id, "code": code, "sw": sw}
    except Exception as e:
        return {"stock_id": stock_id, "code": code, "sw": None, "error": str(e)[:200]}
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--workers", type=int, default=6,
        help="并发线程数（默认6 —— 底层数据源是爬百度股市通，并发太高容易被限流打空）",
    )
    parser.add_argument("--limit", type=int, default=0, help="最多处理N只（0=全部，测试用）")
    parser.add_argument("--normalize", action="store_true", help="补完后顺带跑一次全量归一化")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    stocks = conn.execute(
        """SELECT id, code, name, industry FROM stocks
           WHERE is_active=1 AND (industry_sw IS NULL OR industry_sw='')
           ORDER BY id"""
    ).fetchall()
    conn.close()

    if args.limit:
        stocks = stocks[: args.limit]

    total = len(stocks)
    _stats["total"] = total
    print(f"待补齐: {total} 只  并发: {args.workers} 线程")

    if total == 0:
        print("没有需要补齐的股票，行业分类已全部覆盖。")
        return

    t0 = time.perf_counter()
    done = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(_fetch_one, s["id"], s["code"], s["name"], s["industry"]): s
            for s in stocks
        }
        for fut in as_completed(futures):
            done += 1
            s = futures[fut]
            try:
                r = fut.result()
                if r.get("sw"):
                    _inc("ok")
                else:
                    _inc("fail")
            except Exception as e:
                _inc("fail")

            if done % 100 == 0 or done == total:
                elapsed = time.perf_counter() - t0
                rate = done / elapsed if elapsed > 0 else 0
                eta = (total - done) / rate if rate > 0 else 0
                _log(
                    f"[{done}/{total}] ok={_stats['ok']} fail={_stats['fail']}"
                    f"  速度={rate:.1f}只/s  剩余≈{eta/60:.1f}min"
                )

    elapsed = time.perf_counter() - t0
    print(f"\n完成  耗时={elapsed/60:.1f}min  ok={_stats['ok']}  fail={_stats['fail']}")

    if args.normalize:
        print("\n跑全量归一化 normalize_all_industry_sw()...")
        from services.industry_backfill import normalize_all_industry_sw

        r = normalize_all_industry_sw()
        print(f"归一化: {r.get('count', len(r.get('changed', [])))} 只变更")

    # 收尾: 打印补齐后的覆盖率
    conn2 = sqlite3.connect(DB_PATH)
    total_active = conn2.execute("SELECT COUNT(*) FROM stocks WHERE is_active=1").fetchone()[0]
    covered = conn2.execute(
        "SELECT COUNT(*) FROM stocks WHERE is_active=1 AND industry_sw IS NOT NULL AND industry_sw != ''"
    ).fetchone()[0]
    conn2.close()
    print(f"\n行业分类覆盖率: {covered}/{total_active} ({covered/total_active*100:.1f}%)")


if __name__ == "__main__":
    main()
