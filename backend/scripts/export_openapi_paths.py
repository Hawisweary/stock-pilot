"""R1 API 契约对照:后端 OpenAPI(契约层)× 前端实际调用(行为层)。

用法(在 backend 目录下,后端 :8800 需在运行;或用 --openapi 指定已导出的文件):
    ../venv-quant/bin/python scripts/export_openapi_paths.py
    ../venv-quant/bin/python scripts/export_openapi_paths.py --openapi ../backup/baseline/openapi.json

输出(项目根目录 backup/baseline/):
    openapi.json          后端契约原文
    openapi_paths.csv     契约拍平:method,path,summary,tags,operation_id
    api_ghost.csv         幽灵 API:前端调用了,后端没有
    api_orphan.csv        孤儿 API:后端有,前端没用到(推测类别:内部 / 调度 / 脚本 / 疑似死)
    api_fuzzy.csv         模糊调用:前端路径是动态拼接的,能匹配多个或无法确定
    api_contract.md       汇总

前端路径来自源码静态扫描(lib/api.ts 的 request 自动加 /api 前缀;lib/market.ts 的 API_BASE;
各文件里的 "/api/..." 字面量);页面归属按 import 关系推算。只读,不改任何代码。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import urllib.request
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from baseline_common import BACKEND_DIR, PROJECT_DIR, build_frontend_index, match_score, norm_backend_path, path_match  # noqa: E402

METHODS = ("get", "post", "put", "delete", "patch")
INTERNAL_HINT = re.compile(r"/(system|admin|health|jobs?|internal|debug|warm|cache|sync|metrics|fetch-status|status)(/|$)", re.I)


def load_openapi(src: str | None, url: str) -> dict:
    if src:
        return json.load(open(src, encoding="utf-8"))
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def flatten(spec: dict) -> list[dict]:
    rows = []
    for path, ops in spec.get("paths", {}).items():
        for m, op in ops.items():
            if m in METHODS:
                rows.append({
                    "method": m.upper(), "path": path, "summary": (op.get("summary") or "").strip(),
                    "tags": ",".join(op.get("tags") or []), "operation_id": op.get("operationId") or "",
                })
    return sorted(rows, key=lambda r: (r["path"], r["method"]))


def backend_path_mentions() -> dict:
    """后端源码(脚本、服务)里以字符串出现的 /api/... 路径 → 文件,用于识别被脚本调用的接口。"""
    hits = defaultdict(set)
    pat = re.compile(r"[\"'](/api/[^\"'\s?]*)")
    for dirpath, dirnames, filenames in os.walk(BACKEND_DIR):
        dirnames[:] = [d for d in dirnames if d not in {"__pycache__", "data", "logs"}]
        for fn in filenames:
            if fn.endswith((".py", ".sh")):
                p = os.path.join(dirpath, fn)
                try:
                    for m in pat.finditer(open(p, encoding="utf-8").read()):
                        hits[re.sub(r"\{[^}]*\}", "{}", m.group(1)).rstrip("/")].add(os.path.relpath(p, BACKEND_DIR))
                except UnicodeDecodeError:
                    pass
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--openapi", help="已导出的 openapi.json;不给则从 --url 拉取")
    ap.add_argument("--url", default="http://localhost:8800/openapi.json")
    ap.add_argument("--out", default=os.path.join(PROJECT_DIR, "backup", "baseline"))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    spec = load_openapi(args.openapi, args.url)
    if not args.openapi:
        json.dump(spec, open(os.path.join(args.out, "openapi.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    rows = flatten(spec)
    with open(os.path.join(args.out, "openapi_paths.csv"), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["method", "path", "summary", "tags", "operation_id"])
        w.writeheader()
        w.writerows(rows)

    fe = build_frontend_index()
    uses = []  # (method, norm_path, file, pages)
    for f, mps in fe.file_paths.items():
        for m, p in mps:
            uses.append((m, p, f, sorted(fe.pages_of.get(f, set()))))
    be = [(r["method"], norm_backend_path(r["path"]), r) for r in rows]

    ghost, fuzzy = [], []
    used_ops = set()
    for m, p, f, pages in uses:
        scored = [(match_score(bp, p), r) for bm, bp, r in be]
        scored = [(sc, r) for sc, r in scored if sc >= 0]
        best = max((sc for sc, _ in scored), default=-1)
        cands = [r for sc, r in scored if sc == best]  # 只保留最像的那一组
        same = [r for r in cands if r["method"] == m]
        dynamic = len({r["path"] for r in cands}) > 1
        # 前端方法识别不到时默认为 GET;路径能匹配上就认为在用
        for r in (same or cands):
            used_ops.add((r["method"], r["path"]))
        where = "、".join(pages) or f"(未挂到页面:{f})"
        if not cands:
            closest = min((bp for _, bp, _ in be), key=lambda bp: _distance(bp, p), default="")
            ghost.append({"page": where, "file": f, "method": m, "frontend_path": p, "closest_backend": closest})
        elif dynamic:
            fuzzy.append({"page": where, "file": f, "frontend_path": p,
                          "matches": " | ".join(sorted({f'{r["method"]} {r["path"]}' for r in cands}))})
        elif not same:
            ghost.append({"page": where, "file": f, "method": m, "frontend_path": p,
                          "closest_backend": "路径存在但方法不同:" + ",".join(sorted({r["method"] for r in cands}))})

    mentions = backend_path_mentions()
    orphan = []
    for bm, bp, r in be:
        if (r["method"], r["path"]) in used_ops:
            continue
        scripts = sorted(f for mp, fs in mentions.items() if path_match(bp, mp) for f in fs)
        if scripts:
            kind = "脚本 / 后端内部调用"
        elif INTERNAL_HINT.search(r["path"]):
            kind = "内部 / 调度(按路径推测)"
        else:
            kind = "疑似死 API"
        orphan.append({**r, "kind": kind, "referenced_by": "、".join(scripts[:5])})

    def dump(name, items, fields):
        with open(os.path.join(args.out, name), "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(items)

    dedup = lambda items, keys: list({tuple(i[k] for k in keys): i for i in items}.values())  # noqa: E731
    ghost = sorted(dedup(ghost, ["file", "method", "frontend_path"]), key=lambda x: (x["page"], x["frontend_path"]))
    fuzzy = sorted(dedup(fuzzy, ["file", "frontend_path"]), key=lambda x: (x["page"], x["frontend_path"]))
    orphan = sorted(orphan, key=lambda x: (x["kind"], x["path"]))
    dump("api_ghost.csv", ghost, ["page", "file", "method", "frontend_path", "closest_backend"])
    dump("api_orphan.csv", orphan, ["method", "path", "summary", "tags", "operation_id", "kind", "referenced_by"])
    dump("api_fuzzy.csv", fuzzy, ["page", "file", "frontend_path", "matches"])

    kinds = defaultdict(int)
    for o in orphan:
        kinds[o["kind"]] += 1
    md = [
        "# API 契约对照(R1)", "",
        f"- 后端契约:{len(rows)} 个接口(方法 + 路径);前端被用到的:{len(used_ops)} 个",
        f"- 前端调用点:{len(uses)} 处(去重后的 方法 + 路径 + 文件);前端页面 {len(fe.routes)} 个",
        f"- 幽灵 API {len(ghost)} 个、孤儿 API {len(orphan)} 个({dict(kinds)})、模糊调用 {len(fuzzy)} 处", "",
        "## 幽灵 API(前端调用了,后端没有)",
        "| 页面 | 前端路径 | 方法 | 最接近的后端路径 |", "|---|---|---|---|",
        *[f'| {g["page"]} | `{g["frontend_path"]}` | {g["method"]} | `{g["closest_backend"]}` |' for g in ghost],
        "", "## 模糊调用(动态拼接,能匹配多个后端路径)",
        "| 页面 | 前端路径 | 可能匹配 |", "|---|---|---|",
        *[f'| {z["page"]} | `{z["frontend_path"]}` | {z["matches"]} |' for z in fuzzy],
        "", "## 孤儿 API(按推测类别;\"疑似死 API\"是后续删除候选,删除前人工确认)",
        "| 类别 | 方法 | 路径 | 说明 |", "|---|---|---|---|",
        *[f'| {o["kind"]} | {o["method"]} | `{o["path"]}` | {o["summary"][:40]} |' for o in orphan],
    ]
    open(os.path.join(args.out, "api_contract.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")
    print(f"契约 {len(rows)} 个接口;前端用到 {len(used_ops)};幽灵 {len(ghost)};孤儿 {len(orphan)} {dict(kinds)};模糊 {len(fuzzy)}")
    print(f"输出目录:{args.out}")
    return 0


def _distance(a: str, b: str) -> int:
    sa, sb = a.strip("/").split("/"), b.strip("/").split("/")
    common = 0
    for x, y in zip(sa, sb):
        if x == y or "{}" in (x, y):
            common += 1
        else:
            break
    return (max(len(sa), len(sb)) - common) * 10 + abs(len(sa) - len(sb))


if __name__ == "__main__":
    sys.exit(main())
