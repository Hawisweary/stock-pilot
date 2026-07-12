"""Generate HTML report from policy_scan_*.json snapshot."""
import json, sys, html
from pathlib import Path

src = Path("/Users/harrywang/WorkBuddy/2026-05-18-task-7/ai-fundamental-researcher/data/policy_scan_2026-06-23.json")
d = json.loads(src.read_text())
date = d.get("date")
macro = d.get("macro", "")
snapshot = d.get("snapshot", {})
stocks_updated = d.get("stocks_updated", 0)
industries_scanned = d.get("industries_scanned", len(snapshot))

# 分类
high, mid, low = [], [], []
for name, v in snapshot.items():
    score = v.get("score", 0)
    tendency = v.get("tendency", "中性")
    summary = v.get("summary", "")
    item = {"name": name, "score": score, "tendency": tendency, "summary": summary, "raw": v}
    if score >= 70: high.append(item)
    elif score >= 50: mid.append(item)
    else: low.append(item)

def score_class(s):
    if s >= 70: return "high"
    if s >= 50: return "mid"
    return "low"

def tend_class(t):
    if t in ("强力支持", "支持"): return "strong"
    if t in ("限制", "打压"): return "weak"
    return "neutral"

def industry_block(name, score, tendency, summary):
    cls = score_class(score)
    tcls = tend_class(tendency)
    return (
        f'<div class="industry">'
        f'<div class="score {cls}">{score}</div>'
        f'<div class="detail">'
        f'<div class="name">{html.escape(name)}<span class="tendency {tcls}">{html.escape(tendency)}</span></div>'
        f'<div class="summary">{html.escape(summary)}</div>'
        f'</div></div>'
    )

industry_html = "".join(industry_block(i["name"], i["score"], i["tendency"], i["summary"]) for i in (high + mid + low))

# 排序输出
high_sorted = sorted(high, key=lambda x: -x["score"])
mid_sorted = sorted(mid, key=lambda x: -x["score"])
low_sorted = sorted(low, key=lambda x: -x["score"])
all_sorted = high_sorted + mid_sorted + low_sorted
industry_html = "".join(industry_block(i["name"], i["score"], i["tendency"], i["summary"]) for i in all_sorted)

css = """
body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; margin: 40px; background: #f5f5f5; }
.card { background: #fff; border-radius: 12px; padding: 30px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,.08); }
h1 { color: #1a1a2e; font-size: 24px; }
.stats { display: flex; gap: 16px; margin: 16px 0; }
.stat { background: #e8f4fd; border-radius: 8px; padding: 12px 20px; }
.stat .label { color: #666; font-size: 12px; }
.stat .value { color: #1890ff; font-size: 22px; font-weight: 700; }
.macro { background: #fffbe6; border-left: 4px solid #faad14; padding: 12px 16px; border-radius: 6px; margin: 16px 0; }
.industry { display: flex; align-items: flex-start; padding: 14px 0; border-bottom: 1px solid #f0f0f0; }
.industry:last-child { border-bottom: none; }
.score { min-width: 48px; height: 48px; border-radius: 8px; display: flex; align-items: center; justify-content: center; font-size: 18px; font-weight: 700; color: #fff; margin-right: 16px; }
.score.high { background: #52c41a; }
.score.mid { background: #faad14; }
.score.low { background: #ff4d4f; }
.detail { flex: 1; }
.detail .name { font-weight: 600; font-size: 15px; margin-bottom: 4px; }
.detail .summary { color: #555; font-size: 13px; }
.tendency { font-size: 11px; padding: 2px 8px; border-radius: 10px; margin-left: 6px; }
.tendency.strong { background: #f6ffed; color: #52c41a; }
.tendency.neutral { background: #e6f7ff; color: #1890ff; }
.tendency.weak { background: #fff1f0; color: #ff4d4f; }
.footer { color: #999; font-size: 12px; text-align: center; margin-top: 20px; }
"""

out = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>每日政策面全局扫描 - {date}</title>
<style>{css}</style>
</head>
<body>
<h1>📊 每日政策面全局扫描</h1>
<p style="color:#888">扫描日期: {date} | 状态: {d.get('status','')} | 行业: {industries_scanned} | 股票: {stocks_updated}</p>

<div class="stats">
  <div class="stat"><div class="label">高分行业 ≥70</div><div class="value">{len(high)}</div></div>
  <div class="stat"><div class="label">中分行业 50-69</div><div class="value">{len(mid)}</div></div>
  <div class="stat"><div class="label">低分行业 &lt;50</div><div class="value">{len(low)}</div></div>
</div>

<div class="macro">🏛️ <strong>宏观政策总览：</strong>{html.escape(macro)}</div>

<div class="card">
<h2 style="margin-top:0">行业评分明细（按分数排序）</h2>
{industry_html}
</div>
<div class="footer">AI 基本面研究员 · 全自动生成 · {date}</div>
</body>
</html>
"""

dst = src.with_suffix(".html")
dst.write_text(out)
print(f"WROTE {dst}  bytes={len(out)}")
print(f"high={len(high)} mid={len(mid)} low={len(low)}")
print("TOP:", [(i['name'], i['score'], i['tendency']) for i in high_sorted])
print("BOTTOM:", [(i['name'], i['score'], i['tendency']) for i in low_sorted])
