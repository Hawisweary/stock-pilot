"""
全市场股票入库 — 一次性脚本
从 akshare stock_zh_a_spot_em 拉取 A 股全部股票，批量写入 stocks 表。

用法：
  cd backend
  ../venv-quant/bin/python scripts/import_all_stocks.py
  ../venv-quant/bin/python scripts/import_all_stocks.py --exclude-st  # 排除 ST 股
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config


def _code_to_market(code: str) -> str:
    if code.startswith("6"):
        return "SH"
    if code.startswith(("0", "3")):
        return "SZ"
    if code.startswith("4") or code.startswith("8"):
        return "BJ"
    return "A"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exclude-st", action="store_true", help="排除 ST/*ST 股票")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写库")
    args = parser.parse_args()

    print("正在从 akshare 拉取全市场股票列表 …")
    import akshare as ak
    df = ak.stock_zh_a_spot_em()
    df = df[["代码", "名称"]].dropna()
    df.columns = ["code", "name"]

    if args.exclude_st:
        before = len(df)
        df = df[~df["name"].str.contains(r"ST|退", na=False)]
        print(f"排除 ST/退市：{before} → {len(df)}")

    print(f"共 {len(df)} 只股票")

    if args.dry_run:
        print(df.head(10).to_string())
        return

    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row

    existing = {r[0] for r in conn.execute("SELECT code FROM stocks").fetchall()}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    added = skipped = 0
    rows_to_insert = []
    for _, row in df.iterrows():
        code = str(row["code"]).zfill(6)
        name = str(row["name"])
        if code in existing:
            skipped += 1
            continue
        market = _code_to_market(code)
        rows_to_insert.append((code, name, market, 1, now, now))

    if rows_to_insert:
        conn.executemany(
            "INSERT OR IGNORE INTO stocks (code, name, market, is_active, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            rows_to_insert,
        )
        conn.commit()
        added = len(rows_to_insert)

    conn.close()
    print(f"完成：新增 {added} 只，已存在跳过 {skipped} 只")
    print("下一步：运行 scripts/sync_quotes_batch.py 同步历史行情")


if __name__ == "__main__":
    main()
