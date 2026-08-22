"""为美股 ETF 生成中文投研简介(DeepSeek),缓存回 us_etf_profile.data.cn_intro。

结构化五段:是什么/跟踪/持仓特征/适合谁/风险。用已有结构化数据(类别/持仓/板块/英文原文)。
可复用:默认只补缺 cn_intro 的;--force 全量重生成;--limit N 试跑。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from services.llm_client import chat_completion

SYS = "你是专业的 ETF 投资顾问。基于给定数据,用简洁中文写投研简介。只输出 JSON,不要多余文字。"

TMPL = """为这只美股 ETF 写中文简介。数据:
代码:{ticker}  名称:{name}
类别:{category}  发行商:{family}  规模:{aum}
资产配置:{alloc}
前十持仓:{holdings}
板块权重:{sectors}
英文说明(参考):{overview}

输出 JSON,五个字段各一句话(每句≤45字,客观、不吹嘘、不构成投资建议):
{{"what":"这只ETF是什么(跟踪什么/什么类型)","tracks":"跟踪的指数或投资策略","holdings":"持仓特征(集中度/龙头/风格)","fit":"适合什么样的投资者","risk":"主要风险提示"}}"""


def _fmt(p: dict) -> dict:
    aum = p.get("aum")
    alloc = p.get("asset_classes") or {}
    alloc_s = " ".join(f"{k}:{round(v*100)}%" for k, v in alloc.items() if v and v > 0.005) or "—"
    hs = p.get("holdings") or []
    hold_s = " ".join(f"{h['symbol']}({round((h.get('weight') or 0)*100,1)}%)" for h in hs[:10]) or "无(债券/大宗类)"
    secs = sorted(p.get("sectors") or [], key=lambda s: -(s.get("weight") or 0))[:5]
    sec_s = " ".join(f"{s['sector']}:{round((s.get('weight') or 0)*100)}%" for s in secs) or "—"
    return {
        "category": p.get("category") or "—", "family": p.get("fund_family") or "—",
        "aum": f"${round(aum/1e9,1)}B" if aum else "—",
        "alloc": alloc_s, "holdings": hold_s, "sectors": sec_s,
        "overview": (p.get("overview") or "")[:400],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="全量重生成")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    conn = sqlite3.connect(config.US_DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT ticker, data FROM us_etf_profile ORDER BY ticker").fetchall()
    todo = []
    for r in rows:
        p = json.loads(r["data"])
        if args.force or not p.get("cn_intro"):
            todo.append((r["ticker"], p))
    if args.limit:
        todo = todo[: args.limit]
    print(f"待生成 {len(todo)}/{len(rows)} 只")

    ok = 0
    for i, (tk, p) in enumerate(todo, 1):
        prompt = TMPL.format(ticker=tk, name=p.get("name") or tk, **_fmt(p))
        try:
            txt = chat_completion(prompt, system_prompt=SYS, max_tokens=500,
                                  temperature=0.4, json_mode=True, max_retries=2)
            intro = json.loads(txt)
            if not isinstance(intro, dict) or "what" not in intro:
                raise ValueError("bad shape")
            p["cn_intro"] = {k: intro.get(k) for k in ("what", "tracks", "holdings", "fit", "risk")}
            conn.execute("UPDATE us_etf_profile SET data=? WHERE ticker=?",
                         (json.dumps(p, ensure_ascii=False), tk))
            conn.commit()
            ok += 1
            if i <= 2:
                print(f"  [{tk}] {p['cn_intro']}")
        except Exception as e:  # noqa: BLE001
            print(f"  [{tk}] 失败: {str(e)[:60]}")
        if i % 20 == 0:
            print(f"  ...{i}/{len(todo)}")
        time.sleep(0.4)
    print(f"完成 {ok}/{len(todo)}")
    conn.close()


if __name__ == "__main__":
    main()
