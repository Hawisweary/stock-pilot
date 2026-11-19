"""P4 库表与文件清理(处置清单见 docs/P4处置清单.md)。

默认只打印计划(dry-run),不改任何东西:
    cd backend && ../venv-quant/bin/python scripts/p4_cleanup.py

确认后执行(在主机上、后端调度空闲时):
    ../venv-quant/bin/python scripts/p4_cleanup.py --execute            # 归档并删表、删文件
    ../venv-quant/bin/python scripts/p4_cleanup.py --execute --vacuum   # 另外对主库做 VACUUM(要几分钟,需要约一倍主库大小的空闲磁盘)

删表前先把整张表导出到 backup/archive/<时间戳>/<库>.<表>.parquet,并核对导出行数与表行数一致,
不一致就跳过这张表。删文件只删清单里列明的类别,0 字节文件要求确实是 0 字节。
"""
from __future__ import annotations

import argparse
import glob
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)
from config import CACHE_DB_PATH, DATA_DIR, DB_PATH  # noqa: E402

PROJECT = os.path.dirname(BACKEND)

# (库, 表, 原因) —— 与 docs/P4处置清单.md 2.5 / 2.6 一致
TABLES = [
    ("afr", "score_gap_log", "旧副本:代码已改用 cache.db 同名表"),
    ("afr", "data_fetch_log", "旧副本:代码已改用 cache.db 同名表"),
    ("afr", "factor_metrics_cache", "旧副本:代码已改用 cache.db 同名表"),
    ("afr", "us_daily_quotes", "旧副本:美股已迁到 us.db"),
    ("afr", "us_stocks", "旧副本:美股已迁到 us.db"),
    ("afr", "debate_analyses", "零引用"),
    ("afr", "debate_batch_log", "辩论批处理已下线(v3.0)"),
    ("afr", "multicyc_scores", "多周期评分功能已删除"),
    ("afr", "risk_events", "一次性写入,无人读取,写入代码已删除"),
    ("afr", "market_regime_cluster_daily", "聚类实验已删除,从未产出数据"),
]
DBS = {"afr": DB_PATH, "cache": CACHE_DB_PATH}

ZERO_BYTE_DBS = ["ai_fundamental", "ai_research", "finance", "financial_data", "market_data",
                 "stock_analysis", "stock_data", "stocks"]
MANUAL_BACKUP_GLOBS = ["afr.db.bak_*", "afr.db.*_backup_*"]


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def plan_files() -> list[tuple[str, str]]:
    items = []
    for name in ZERO_BYTE_DBS:
        p = os.path.join(DATA_DIR, f"{name}.db")
        if os.path.exists(p) and os.path.getsize(p) == 0:
            items.append((p, "0 字节空库,无引用"))
    for pat in MANUAL_BACKUP_GLOBS:
        for p in sorted(glob.glob(os.path.join(DATA_DIR, pat))):
            items.append((p, "历次大改前的手工备份"))
    for p in sorted(glob.glob(os.path.join(DATA_DIR, "backups", "afr_backup_*.db"))):
        items.append((p, "自动备份压缩失败留下的未压缩残留(自动轮转只清理 .gz)"))
    p = os.path.join(DATA_DIR, "afr_read.db")
    if os.path.exists(p):
        items.append((p, "只读副本,已停止生成,读取方改读主库"))
    return items


def table_rows(conn, table: str) -> int | None:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        return None
    return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]


def archive_table(conn, table: str, out_path: str) -> int:
    import pandas as pd

    df = pd.read_sql_query(f'SELECT * FROM "{table}"', conn)
    df.to_parquet(out_path, index=False)
    return len(pd.read_parquet(out_path))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--execute", action="store_true", help="真正执行;不加只打印计划")
    ap.add_argument("--vacuum", action="store_true", help="删表后对主库 VACUUM")
    ap.add_argument("--skip-files", action="store_true")
    ap.add_argument("--skip-tables", action="store_true")
    args = ap.parse_args()
    mode = "执行" if args.execute else "计划(dry-run,不会改动任何东西)"
    print(f"== P4 清理 · {mode} · 数据目录 {DATA_DIR}")
    if os.getenv("AFR_SCHEDULER_ENABLED", "1") == "0":
        print("   提示:本机调度器关闭,看起来是开发机;正式清理应在主机执行")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_dir = os.path.join(PROJECT, "backup", "archive", stamp)

    # ── 表 ──
    if not args.skip_tables:
        print("\n[表] 先归档为 parquet,核对行数后删除")
        conns = {k: sqlite3.connect(v, timeout=30) for k, v in DBS.items() if os.path.exists(v)}
        for db, table, why in TABLES:
            conn = conns.get(db)
            n = table_rows(conn, table) if conn else None
            if n is None:
                print(f"   - {db}.{table}: 不存在,跳过")
                continue
            print(f"   - {db}.{table}: {n} 行 · {why}")
            if not args.execute:
                continue
            os.makedirs(archive_dir, exist_ok=True)
            out = os.path.join(archive_dir, f"{db}.{table}.parquet")
            got = archive_table(conn, table, out)
            if got != n:
                print(f"     ! 归档行数 {got} ≠ 表行数 {n},跳过删除")
                continue
            conn.execute(f'DROP TABLE "{table}"')
            conn.commit()
            print(f"     已归档 → {os.path.relpath(out, PROJECT)},已删除")
        if args.execute and args.vacuum and "afr" in conns:
            before = os.path.getsize(DB_PATH)
            print(f"\n[VACUUM] 主库 {human(before)} …")
            t0 = time.time()
            conns["afr"].execute("VACUUM")
            print(f"   完成:{human(before)} → {human(os.path.getsize(DB_PATH))}({time.time() - t0:.0f}s)")
        for c in conns.values():
            c.close()

    # ── 文件 ──
    if not args.skip_files:
        files = plan_files()
        total = sum(os.path.getsize(p) for p, _ in files)
        print(f"\n[文件] {len(files)} 个,共 {human(total)}")
        for p, why in files:
            print(f"   - {os.path.relpath(p, PROJECT)}  {human(os.path.getsize(p))} · {why}")
        if args.execute:
            for p, _ in files:
                os.remove(p)
                for suffix in ("-wal", "-shm"):
                    if os.path.exists(p + suffix):
                        os.remove(p + suffix)
            print(f"   已删除,腾出约 {human(total)};磁盘剩余 {human(shutil.disk_usage(DATA_DIR).free)}")

    if not args.execute:
        print("\n确认无误后加 --execute 执行(建议在主机、调度空闲时)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
