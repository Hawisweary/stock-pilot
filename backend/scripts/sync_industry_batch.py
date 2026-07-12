"""
批量同步申万行业分类 — 用 akshare stock_board_industry_name_em + 成分股
每周运行一次即可，覆盖全市场。

用法：
  cd backend
  ../venv-quant/bin/python scripts/sync_industry_batch.py
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config


def main():
    import akshare as ak

    print("拉取申万一级行业列表 …")
    try:
        industry_df = ak.stock_board_industry_name_em()
    except Exception as e:
        print(f"获取行业列表失败: {e}")
        return

    industries = industry_df["板块名称"].tolist()
    print(f"共 {len(industries)} 个行业板块")

    conn = sqlite3.connect(config.DB_PATH)
    code_to_id = {r[0]: r[1] for r in conn.execute("SELECT code, id FROM stocks").fetchall()}

    updated = 0
    for ind in industries:
        try:
            df = ak.stock_board_industry_cons_em(symbol=ind)
            if df is None or df.empty:
                continue
            code_col = "代码" if "代码" in df.columns else df.columns[1]
            for _, row in df.iterrows():
                code = str(row[code_col]).zfill(6)
                sid = code_to_id.get(code)
                if sid:
                    conn.execute(
                        "UPDATE stocks SET industry_sw=?, updated_at=datetime('now') WHERE id=?",
                        (ind, sid),
                    )
                    updated += 1
            time.sleep(0.3)
        except Exception as e:
            print(f"  {ind}: {e}")

    conn.commit()
    conn.close()
    print(f"完成：更新 {updated} 只股票行业分类")


if __name__ == "__main__":
    main()
