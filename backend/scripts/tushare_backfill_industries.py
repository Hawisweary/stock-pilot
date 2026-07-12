"""用 Tushare Pro 申万分类批量补齐 stocks.industry_sw / industry_sw2 / industry_sw3 — 替代 adata 爬百度股市通的方案

一次调用 index_classify + 31 次 index_member_all 覆盖全市场（一级+二级+三级），比逐股票
查询快得多、稳定得多（用法：python3 scripts/tushare_backfill_industries.py）
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config


def main() -> None:
    from services.tushare_adapter import fetch_industry_map

    print("拉取全市场申万一级/二级/三级行业分类（Tushare Pro）...")
    t0 = time.perf_counter()
    industry_map = fetch_industry_map()
    print(f"拉取完成: {len(industry_map)} 只股票，耗时 {time.perf_counter()-t0:.1f}s")

    conn = sqlite3.connect(config.DB_PATH)
    stocks = conn.execute(
        "SELECT id, code, industry_sw, industry_sw2, industry_sw3 FROM stocks WHERE is_active=1"
    ).fetchall()

    updated_l1 = 0
    updated_l2 = 0
    updated_l3 = 0
    skipped_no_match = 0
    for stock_id, code, old_sw, old_sw2, old_sw3 in stocks:
        info = industry_map.get(code)
        if not info:
            skipped_no_match += 1
            continue
        new_sw = info["l1"]
        new_sw2 = info["l2"] or new_sw
        new_sw3 = info["l3"] or new_sw2
        if new_sw != old_sw or new_sw2 != old_sw2 or new_sw3 != old_sw3:
            conn.execute(
                """UPDATE stocks SET industry_sw=?, industry_sw2=?, industry_sw3=?,
                   industry=COALESCE(NULLIF(industry,''), ?) WHERE id=?""",
                (new_sw, new_sw2, new_sw3, new_sw, stock_id),
            )
            if new_sw != old_sw:
                updated_l1 += 1
            if new_sw2 != old_sw2:
                updated_l2 += 1
            if new_sw3 != old_sw3:
                updated_l3 += 1
    conn.commit()

    total_active = conn.execute("SELECT COUNT(*) FROM stocks WHERE is_active=1").fetchone()[0]
    covered_l1 = conn.execute(
        "SELECT COUNT(*) FROM stocks WHERE is_active=1 AND industry_sw IS NOT NULL AND industry_sw != ''"
    ).fetchone()[0]
    covered_l2 = conn.execute(
        "SELECT COUNT(*) FROM stocks WHERE is_active=1 AND industry_sw2 IS NOT NULL AND industry_sw2 != ''"
    ).fetchone()[0]
    covered_l3 = conn.execute(
        "SELECT COUNT(*) FROM stocks WHERE is_active=1 AND industry_sw3 IS NOT NULL AND industry_sw3 != ''"
    ).fetchone()[0]
    conn.close()

    print(f"\nL1更新: {updated_l1}  L2更新: {updated_l2}  L3更新: {updated_l3}  Tushare无匹配: {skipped_no_match}")
    print(f"一级行业覆盖率: {covered_l1}/{total_active} ({covered_l1/total_active*100:.1f}%)")
    print(f"二级行业覆盖率: {covered_l2}/{total_active} ({covered_l2/total_active*100:.1f}%)")
    print(f"三级行业覆盖率: {covered_l3}/{total_active} ({covered_l3/total_active*100:.1f}%)")


if __name__ == "__main__":
    main()
