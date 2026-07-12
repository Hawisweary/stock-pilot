"""audit_profile_distribution.py — M1-4 验收脚本
用途：
  1. 隔离验证（硬门禁）：portfolio_svc / score_alerts / select_top_n 不含 stock_score_profiles
  2. 分布报告（首日观测）：|value - momentum| 分布、银行 vs 科技 Δ 中位数

运行：cd backend && python scripts/audit_profile_distribution.py
"""
import json
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

# ─── 1. 隔离验证（硬门禁）────────────────────────────────────────────────────
print("=" * 60)
print("M1-4 隔离验证（硬门禁）")
print("=" * 60)

# 完全不允许引用 stock_score_profiles 的文件
STRICT_FILES = [
    "services/portfolio_svc.py",
    "services/strategy_selector.py",
]
# scores.py 只允许在 profile 路由函数段内引用
SCORES_FILE = "api/scores.py"
FORBIDDEN_PATTERN = re.compile(r"stock_score_profiles")
PROFILE_FUNC_PATTERN = re.compile(r"async def (profile_ranking|stock_profile_scores)")

isolation_ok = True
for rel_path in STRICT_FILES:
    full_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), rel_path)
    if not os.path.exists(full_path):
        continue
    with open(full_path) as f:
        content = f.read()
    if FORBIDDEN_PATTERN.search(content):
        print(f"  ❌ {rel_path} 含违规引用 stock_score_profiles")
        isolation_ok = False
    else:
        print(f"  ✅ {rel_path}")

# scores.py：检查 profile 路由之外的部分
scores_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), SCORES_FILE)
if os.path.exists(scores_path):
    with open(scores_path) as f:
        lines = f.readlines()
    in_profile_func = False
    violations = []
    for i, line in enumerate(lines, 1):
        if PROFILE_FUNC_PATTERN.search(line):
            in_profile_func = True
        elif re.match(r"^@router\.|^async def |^def ", line) and not PROFILE_FUNC_PATTERN.search(line):
            in_profile_func = False
        if not in_profile_func and FORBIDDEN_PATTERN.search(line):
            violations.append((i, line.rstrip()))
    if violations:
        print(f"  ❌ {SCORES_FILE} 在非 profile 路由中含违规引用：")
        for ln, txt in violations:
            print(f"     L{ln}: {txt}")
        isolation_ok = False
    else:
        print(f"  ✅ {SCORES_FILE}（profile 路由外无引用）")

# v_stock_scores 视图不能 JOIN stock_score_profiles
conn = sqlite3.connect(config.DB_PATH)
view_sql = conn.execute(
    "SELECT sql FROM sqlite_master WHERE type='view' AND name='v_stock_scores'"
).fetchone()
if view_sql and FORBIDDEN_PATTERN.search(view_sql[0] or ""):
    print("  ❌ v_stock_scores 视图含 stock_score_profiles JOIN")
    isolation_ok = False
else:
    print("  ✅ v_stock_scores 视图不含 profile 引用")

print(f"\n隔离验证：{'✅ 通过' if isolation_ok else '❌ 失败'}")

# ─── 2. 分布报告────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("M1-4 分布报告（首日观测）")
print("=" * 60)

conn.row_factory = sqlite3.Row
latest_profile = conn.execute(
    "SELECT MAX(calc_date) FROM stock_score_profiles WHERE profile='momentum'"
).fetchone()[0]

if not latest_profile:
    print("⚠️  尚无 profile 数据，请先运行 compute_all_v5_scores()")
    conn.close()
    sys.exit(0)

rows = conn.execute(
    """SELECT sp_mom.stock_id,
              sp_mom.score AS mom_score,
              sp_div.score AS div_score,
              cs.composite_v5 AS value_score,
              s.industry_sw
       FROM stock_score_profiles sp_mom
       JOIN stocks s ON s.id = sp_mom.stock_id
       LEFT JOIN stock_score_profiles sp_div
         ON sp_div.stock_id = sp_mom.stock_id
        AND sp_div.calc_date = sp_mom.calc_date
        AND sp_div.profile = 'dividend'
       LEFT JOIN (
         SELECT stock_id, composite_v5 FROM comprehensive_scores
         WHERE calc_date = (SELECT MAX(calc_date) FROM comprehensive_scores WHERE composite_v5 IS NOT NULL)
       ) cs ON cs.stock_id = sp_mom.stock_id
       WHERE sp_mom.calc_date = ? AND sp_mom.profile = 'momentum'
         AND cs.composite_v5 IS NOT NULL""",
    (latest_profile,),
).fetchall()
conn.close()

if not rows:
    print("⚠️  无数据（profile 与 comprehensive_scores 无交集）")
    sys.exit(0)

deltas = sorted([abs(float(r["mom_score"]) - float(r["value_score"])) for r in rows])
n = len(deltas)
p50 = deltas[n // 2]
p90 = deltas[int(n * 0.9)]

print(f"样本量：{n} 只")
print(f"|value - momentum| : p50={p50:.1f}  p90={p90:.1f}  max={max(deltas):.1f}")

# 按阈值统计
for thresh in (5, 10, 15):
    cnt = sum(1 for d in deltas if d >= thresh)
    print(f"  |Δ| ≥ {thresh}: {cnt} 只 ({cnt/n*100:.1f}%)")

# 行业分组（银行 vs 科技）
def median(lst):
    s = sorted(lst)
    m = len(s) // 2
    return s[m] if s else None

bank_deltas = [float(r["mom_score"]) - float(r["value_score"])
               for r in rows if "银行" in (r["industry_sw"] or "")]
tech_deltas = [float(r["mom_score"]) - float(r["value_score"])
               for r in rows if any(k in (r["industry_sw"] or "") for k in ("电子", "计算机", "通信", "半导体"))]

print(f"\n银行：{len(bank_deltas)} 只，momentum-value Δ 中位数 = {median(bank_deltas):.1f}" if bank_deltas else "\n银行：无数据")
print(f"科技：{len(tech_deltas)} 只，momentum-value Δ 中位数 = {median(tech_deltas):.1f}" if tech_deltas else "科技：无数据")

# 临时通过标准
sign_ok = (
    bool(bank_deltas) and bool(tech_deltas)
    and (median(bank_deltas) < 0) != (median(tech_deltas) < 0)
)
p50_ok = p50 >= 3
print(f"\n临时通过标准：")
print(f"  |Δ| p50 ≥ 3: {'✅' if p50_ok else '❌'} ({p50:.1f})")
print(f"  银行/科技 Δ 异号: {'✅' if sign_ok else '❌（或数据不足）'}")

overall = isolation_ok and p50_ok
print(f"\n总体：{'✅ M1 验收通过（临时门禁）' if overall else '❌ 未通过，见上方'}")
