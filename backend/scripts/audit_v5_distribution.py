"""audit_v5_distribution.py — G-Discount / G-Capital 门禁验收。
运行：cd backend && python scripts/audit_v5_distribution.py
"""
import json
import sqlite3
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

conn = sqlite3.connect(config.DB_PATH)
conn.row_factory = sqlite3.Row

latest = conn.execute(
    "SELECT MAX(calc_date) FROM comprehensive_scores WHERE composite_v5 IS NOT NULL"
).fetchone()[0]

if not latest:
    print("❌ 无 composite_v5 数据，请先跑 compute_all_v5_scores()")
    sys.exit(1)

rows = conn.execute(
    """SELECT s.code, s.name, cs.composite_v5, cs.capital_score, cs.veto_status
       FROM comprehensive_scores cs
       JOIN stocks s ON s.id = cs.stock_id
       WHERE cs.calc_date = ? AND cs.composite_v5 IS NOT NULL""",
    (latest,),
).fetchall()

pool = len(rows)
scored = pool
discount = sum(1 for r in rows if r["veto_status"] == "discount")
exclude = sum(1 for r in rows if r["veto_status"] == "exclude")
zero = sum(1 for r in rows if r["composite_v5"] == 0.0)
max_score = max(r["composite_v5"] for r in rows)
capital_zero = sum(1 for r in rows if (r["capital_score"] or 0) == 0.0)

sorted_rows = sorted(rows, key=lambda r: r["composite_v5"], reverse=True)
top10_discount = sum(1 for r in sorted_rows[:10] if r["veto_status"] == "discount")

discount_pct = round(discount / pool * 100, 1) if pool else 0
zero_pct = round(zero / pool * 100, 1) if pool else 0
capital_zero_pct = round(capital_zero / pool * 100, 1) if pool else 0

result = {
    "calc_date": latest,
    "pool": pool,
    "scored": scored,
    "discount": discount,
    "discount_pct": discount_pct,
    "exclude": exclude,
    "zero": zero,
    "zero_pct": zero_pct,
    "top10_discount": top10_discount,
    "max_score": round(max_score, 2),
    "capital_zero": capital_zero,
    "capital_zero_pct": capital_zero_pct,
}

print(json.dumps(result, ensure_ascii=False, indent=2))

# 门禁判定
ok = True
print("\n── G-Discount 门禁 ──")
if discount_pct <= 20:
    print(f"  ✅ discount 占比 {discount_pct}% ≤ 20%")
else:
    print(f"  ❌ discount 占比 {discount_pct}% > 20%（目标 ≤20%）")
    ok = False

if zero_pct <= 5:
    print(f"  ✅ 归零占比 {zero_pct}% ≤ 5%（允许真实极差股归零）")
else:
    print(f"  ❌ 归零占比 {zero_pct}% > 5%（目标 ≤5%）")
    ok = False

if top10_discount <= 4:
    print(f"  ✅ Top10 中 discount {top10_discount} 只 ≤ 4")
else:
    print(f"  ❌ Top10 中 discount {top10_discount} 只 > 4（目标 ≤4）")
    ok = False

print("\n── G-Capital 门禁 ──")
if capital_zero_pct <= 20:
    print(f"  ✅ capital_score=0 占比 {capital_zero_pct}% ≤ 20%（市场净流出环境下合理）")
else:
    print(f"  ❌ capital_score=0 占比 {capital_zero_pct}% > 20%（目标 ≤20%）")
    ok = False

# 蓝筹检查
blue_chips = ["600519", "000333"]
print("\n── 蓝筹 capital 档位 ──")
for code in blue_chips:
    r = next((x for x in rows if x["code"] == code), None)
    cap = r["capital_score"] if r else None
    tier_label = {0: "-2", 25: "-1", 50: "0", 75: "1", 100: "2"}.get(int(cap or -1), "?")
    ok_flag = "✅" if cap is not None and cap >= 25 else "❌"
    print(f"  {ok_flag} {code} capital_score={cap}（tier {tier_label}）")

print(f"\n{'✅ 全部门禁通过' if ok else '❌ 存在未通过门禁，见上方'}")
conn.close()
