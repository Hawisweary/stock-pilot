"""P6 数据基线:每张表的写入方、读取方、最近更新、更新频率 / SLA、数据源、磁盘占用。

用法(在 backend 目录下):
    ../venv-quant/bin/python scripts/data_baseline.py                 # 以今天为基准判断是否超时
    ../venv-quant/bin/python scripts/data_baseline.py --as-of latest  # 以库里最新交易日为基准(开发机快照用)
    ../venv-quant/bin/python scripts/data_baseline.py --skip-db       # 只做代码扫描,不读库

输出:项目根目录 backup/baseline/<时间戳>/ 下的 tables.csv、report.md、baseline.json。
重构前跑一次存档,重构后再跑一次对比(见重构清单 P6)。

只读:数据库以 mode=ro 打开;单条查询超过 --query-timeout 秒会中止并记为"超时"。
写入方 / 读取方来自源码静态扫描(按函数名近似的调用图),结论是"可能",用于盘点。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from baseline_common import (  # noqa: E402
    BACKEND_DIR,
    PROJECT_DIR,
    build_backend_index,
    build_frontend_index,
    norm_backend_path,
    path_match,
    reachable,
    reachable_depth,
)

sys.path.insert(0, BACKEND_DIR)
try:
    from config import CACHE_DB_PATH, DB_PATH, US_DB_PATH  # noqa: E402
except Exception:  # 配置读取失败时退回默认位置
    _d = os.path.join(PROJECT_DIR, "data")
    DB_PATH, CACHE_DB_PATH, US_DB_PATH = (os.path.join(_d, n) for n in ("afr.db", "cache.db", "us.db"))

MAIN_DBS = {"afr": DB_PATH, "us": US_DB_PATH, "cache": CACHE_DB_PATH}

# 作业 → 频率类别;SLA 按"交易日"(日频)或自然日(周频)计
JOB_FREQ = {
    "nightly": "nightly",
    "daily": "daily 盘后",
    "morning": "morning 开盘",
    "weekly": "weekly",
    "us_daily": "us_daily",
    "technical_retry": "daily 盘后",
    "regime_l2_l3": "按需",
    "warm_cache": "按需",
    "warm_decay": "按需",
}
FREQ_ORDER = ["nightly", "daily 盘后", "us_daily", "weekly", "morning 开盘", "按需(API)", "按需", "手动脚本", "未知"]
SLA = {  # 频率 -> (单位, 阈值)
    "nightly": ("trading_days", 2),
    "daily 盘后": ("trading_days", 2),
    "us_daily": ("calendar_days", 4),
    "weekly": ("calendar_days", 9),
}
# 只有"数据日期"列才用来判断是否超时;created_at 之类的是业务表 / 日志表的写入时间,不判 SLA
DATA_TIME_COLS = {
    "trade_date", "date", "calc_date", "snapshot_date", "snap_date", "report_date", "ann_date", "end_date",
    "publish_date", "pub_date", "notice_date", "event_date", "as_of",
}
TIME_COLS = [
    "trade_date", "date", "calc_date", "snapshot_date", "snap_date", "report_date", "ann_date", "end_date",
    "publish_date", "pub_date", "notice_date", "event_date", "as_of", "created_at", "updated_at",
    "fetched_at", "computed_at", "ts", "timestamp", "time", "datetime",
]


class _RO:
    """只读连接;每条查询有超时(用 progress handler 中止)。"""

    def __init__(self, path: str, timeout: float):
        self.conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        self.timeout = timeout
        self.deadline = 0.0
        self.conn.set_progress_handler(lambda: 1 if time.monotonic() > self.deadline else 0, 20000)

    def close(self):
        self.conn.close()


def _ro(path: str, timeout: float) -> _RO:
    return _RO(path, timeout)


def _q(ro: _RO, sql, args=()):
    ro.deadline = time.monotonic() + ro.timeout
    return ro.conn.execute(sql, args).fetchall()


def _parse_day(v) -> date | None:
    if v is None:
        return None
    s = str(v).strip()
    try:
        if len(s) >= 8 and s[:8].isdigit():
            return datetime.strptime(s[:8], "%Y%m%d").date()
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        try:  # 时间戳(秒)
            f = float(s)
            if f > 1e9:
                return datetime.fromtimestamp(f if f < 1e12 else f / 1000).date()
        except ValueError:
            pass
    return None


def scan_db(name: str, path: str, timeout: float, dbstat_timeout: float) -> dict:
    out = {}
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return out
    conn = _ro(path, timeout)
    sizes = {}
    conn.timeout = dbstat_timeout  # dbstat 要扫全部页,大库(6GB+)需要几分钟
    try:
        for t, b in _q(conn, "SELECT name, SUM(pgsize) FROM dbstat GROUP BY name"):
            sizes[t] = b
    except sqlite3.Error as e:
        print(f"[{name}] dbstat 未完成({e}),磁盘占用列为 0;可用 --dbstat-timeout 加大", file=sys.stderr)
    conn.timeout = timeout
    tables = [r[0] for r in _q(conn, "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    idx_of = defaultdict(int)
    for tbl, in [(r[0],) for r in _q(conn, "SELECT tbl_name FROM sqlite_master WHERE type='index'")]:
        idx_of[tbl] += 1
    for t in tables:
        info = {"db": name, "table": t, "rows": None, "bytes": sizes.get(t, 0), "time_col": None, "last": None, "note": ""}
        # 表本身 + 它的索引
        info["bytes"] = sizes.get(t, 0) + sum(
            b for n, b in sizes.items() if n.startswith(("idx_", "sqlite_autoindex_" + t)) and t in n
        )
        try:
            cols = [r[1] for r in _q(conn, f'PRAGMA table_info("{t}")')]
            tc = next((c for c in TIME_COLS if c in cols), None)
            info["time_col"] = tc
            info["rows"] = _q(conn, f'SELECT COUNT(*) FROM "{t}"')[0][0]
            if tc and info["rows"]:
                info["last"] = _q(conn, f'SELECT MAX("{tc}") FROM "{t}"')[0][0]
        except sqlite3.OperationalError as e:
            info["note"] = "查询超时" if "interrupt" in str(e).lower() else f"错误:{e}"
        out[t] = info
    conn.close()
    return out


_CAL_CACHE: dict = {}


def trading_days_between(conn_path: str, d0: date, d1: date) -> int | None:
    """(d0, d1] 之间的交易日数;没有交易日历(或日历没覆盖到)时返回 None。"""
    if conn_path not in _CAL_CACHE:
        _CAL_CACHE[conn_path] = _load_calendar(conn_path)
    days = _CAL_CACHE[conn_path]
    if not days or d0 < days[0]:
        return None
    return sum(1 for d in days if d0 < d <= d1)


def _load_calendar(conn_path: str) -> list:
    try:
        conn = sqlite3.connect(f"file:{conn_path}?mode=ro", uri=True, timeout=5)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(trade_calendar)")]
        if not cols:
            return []
        dcol = "cal_date" if "cal_date" in cols else "trade_date" if "trade_date" in cols else "date"
        opn = "is_open" if "is_open" in cols else None
        rows = conn.execute(f"SELECT {dcol}{', ' + opn if opn else ''} FROM trade_calendar").fetchall()
        conn.close()
    except sqlite3.Error:
        return []
    return sorted(d for r in rows if (d := _parse_day(r[0])) and (opn is None or int(r[1] or 0) == 1))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--as-of", default="today", help="today | latest | YYYY-MM-DD")
    ap.add_argument("--skip-db", action="store_true")
    ap.add_argument("--query-timeout", type=float, default=30.0)
    ap.add_argument("--dbstat-timeout", type=float, default=900.0)
    ap.add_argument("--out", default=os.path.join(PROJECT_DIR, "backup", "baseline"))
    args = ap.parse_args()

    t0 = time.time()
    be = build_backend_index()
    fe = build_frontend_index()

    db_tables: dict[str, dict] = {}
    if not args.skip_db:
        for name, path in MAIN_DBS.items():
            for t, info in scan_db(name, path, args.query_timeout, args.dbstat_timeout).items():
                db_tables[f"{name}.{t}"] = info
    known = {k.split(".", 1)[1] for k in db_tables} if db_tables else None

    # ── 写入方 / 读取方(函数级)──
    writers, readers, dyn = defaultdict(set), defaultdict(set), []
    for k, f in be.funcs.items():
        for t in f.writes:
            if known is None or t in known:
                writers[t].add(k)
        for t in f.reads:
            if known is None or t in known:
                readers[t].add(k)
        for ln, s in f.dynamic_sql:
            dyn.append((f.module, f.qualname, ln, s))

    # ── 作业可达 → 频率 ──
    job_reach = {j: reachable_depth(be, entries) for j, entries in be.jobs.items()}
    # ── 路由可达 → API ──
    route_reach = {}
    for r in be.routes:
        route_reach[(r.method, r.path)] = reachable(be, [r.func_key], max_depth=6)
    # 前端:路径 → 页面
    fe_use = []  # (method, path, file, pages)
    for f, mps in fe.file_paths.items():
        pages = fe.pages_of.get(f, set())
        for m, p in mps:
            fe_use.append((m, p, f, pages))

    def pages_for_route(method, path):
        np_ = norm_backend_path(path)
        out = set()
        for m, p, f, pages in fe_use:
            if path_match(np_, p) and (m == method or m == "GET" and method == "GET"):
                out |= pages or {f"(未挂到页面:{f})"}
        return out

    route_pages = {rk: pages_for_route(*rk) for rk in route_reach}

    all_tables = sorted(set(writers) | set(readers) | ({i["table"] for i in db_tables.values()}))
    as_of = date.today()
    if args.as_of == "latest" and "afr.stock_daily_quotes" in db_tables:
        as_of = _parse_day(db_tables["afr.stock_daily_quotes"]["last"]) or as_of
    elif args.as_of not in ("today", "latest"):
        as_of = datetime.strptime(args.as_of, "%Y-%m-%d").date()

    rows = []
    for key in sorted(db_tables) or [f"?.{t}" for t in all_tables]:
        info = db_tables.get(key, {"db": "?", "table": key.split(".", 1)[1], "rows": None, "bytes": 0, "time_col": None, "last": None, "note": ""})
        t = info["table"]
        w = writers.get(t, set())
        rd = readers.get(t, set())
        job_depth = {j: min(reach[k] for k in w if k in reach) for j, reach in job_reach.items() if any(k in reach for k in w)}
        jobs = sorted(job_depth, key=lambda j: (job_depth[j], FREQ_ORDER.index(JOB_FREQ.get(j, "按需"))))
        api_writes = sorted(f"{m} {p}" for (m, p), reach in route_reach.items() if w & reach)
        api_reads = sorted(f"{m} {p}" for (m, p), reach in route_reach.items() if rd & reach)
        pages = sorted({pg for (m, p), reach in route_reach.items() if rd & reach for pg in route_pages[(m, p)]})
        # 主频率 = 调用链最短的作业(深度相同按频率顺序);深度大的多半是同名函数误连
        freqs = list(dict.fromkeys(JOB_FREQ.get(j, "按需") for j in jobs))
        if not freqs:
            freqs = ["按需(API)"] if api_writes else (["手动脚本"] if any(k.startswith("scripts/") for k in w) else ["未知"])
        freq = freqs[0]
        srcs = sorted({s for k in w for s in (be.funcs[k].sources or be.module_sources.get(be.funcs[k].module, set()))} - {"llm"} | (
            {"llm"} if any("llm" in be.funcs[k].sources for k in w) else set()))
        last = _parse_day(info["last"])
        status, lag = "—", None
        if freq in SLA and info.get("rows") and info["time_col"] not in DATA_TIME_COLS:
            status = "—(业务/日志表)"
        elif freq in SLA and info.get("rows"):
            unit, limit = SLA[freq]
            if last is None:
                status = "无时间列" if not info["time_col"] else "无数据"
            else:
                if unit == "trading_days":
                    lag = trading_days_between(DB_PATH, last, as_of)
                    if lag is None:
                        unit, lag = "calendar_days", (as_of - last).days
                else:
                    lag = (as_of - last).days
                status = "超时" if lag > limit else "正常"
        elif not info.get("rows"):
            status = "空表"
        rows.append({
            "db": info["db"], "table": t, "rows": info["rows"], "mb": round((info["bytes"] or 0) / 1e6, 2),
            "time_col": info["time_col"] or "", "last": str(info["last"] or ""), "frequency": freq,
            "all_frequencies": "、".join(freqs), "sla_status": status, "lag": "" if lag is None else lag,
            "jobs": "、".join(f"{j}({job_depth[j]})" for j in jobs), "writers": "; ".join(sorted(w)), "writer_count": len({be.funcs[k].module for k in w}),
            "api_writes": "; ".join(api_writes[:8]), "readers": "; ".join(sorted(rd)[:12]), "reader_count": len(rd),
            "api_reads": "; ".join(api_reads[:12]), "pages": "、".join(pages), "sources": "、".join(srcs), "note": info.get("note", ""),
        })

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    outdir = os.path.join(args.out, stamp)
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "tables.csv"), "w", newline="", encoding="utf-8-sig") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        wr.writeheader()
        wr.writerows(rows)
    with open(os.path.join(outdir, "dynamic_sql.csv"), "w", newline="", encoding="utf-8-sig") as fh:
        wr = csv.writer(fh)
        wr.writerow(["module", "function", "line", "sql"])
        wr.writerows(sorted(dyn))
    src_view = defaultdict(lambda: {"tables": set(), "pages": set()})
    for r in rows:
        for s in filter(None, r["sources"].split("、")):
            src_view[s]["tables"].add(f'{r["db"]}.{r["table"]}')
            src_view[s]["pages"] |= set(filter(None, r["pages"].split("、")))
    meta = {
        "generated_at": datetime.now().isoformat(timespec="seconds"), "as_of": as_of.isoformat(),
        "db_scanned": not args.skip_db, "functions": len(be.funcs), "routes": len(be.routes),
        "jobs": {j: len(v) for j, v in be.jobs.items()}, "frontend_routes": sorted(fe.routes),
        "seconds": round(time.time() - t0, 1),
    }
    with open(os.path.join(outdir, "baseline.json"), "w", encoding="utf-8") as fh:
        json.dump({"meta": meta, "tables": rows,
                   "sources": {k: {"tables": sorted(v["tables"]), "pages": sorted(v["pages"])} for k, v in src_view.items()}},
                  fh, ensure_ascii=False, indent=1)
    _write_report(os.path.join(outdir, "report.md"), rows, meta, dyn, src_view)
    print(f"基线已生成:{outdir}({len(rows)} 张表,{meta['seconds']}s)")
    return 0


def _write_report(path, rows, meta, dyn, src_view):
    def tbl(header, items):
        lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
        lines += ["| " + " | ".join(str(x) for x in it) + " |" for it in items]
        return "\n".join(lines) if items else "(无)"

    stale = [r for r in rows if r["sla_status"] == "超时"]
    multi = sorted([r for r in rows if r["writer_count"] >= 3], key=lambda r: -r["writer_count"])
    no_reader = [r for r in rows if r["reader_count"] == 0 and r["db"] != "?"]
    no_writer = [r for r in rows if not r["writers"] and r["db"] != "?"]
    no_page = [r for r in rows if r["reader_count"] and not r["pages"]]
    biggest = sorted(rows, key=lambda r: -(r["mb"] or 0))[:15]
    by_freq = defaultdict(int)
    for r in rows:
        by_freq[r["frequency"]] += 1
    md = [
        f"# 数据基线 {meta['generated_at']}",
        "",
        f"- 判断超时的基准日:{meta['as_of']};扫描数据库:{'是' if meta['db_scanned'] else '否'};耗时 {meta['seconds']}s",
        f"- 代码:{meta['functions']} 个函数、{meta['routes']} 个 API 路由、调度作业 {meta['jobs']}",
        f"- 前端路由 {len(meta['frontend_routes'])} 个",
        "- 写入方 / 读取方来自源码静态扫描(按函数名近似的调用图),是\"可能写入 / 可能读取\";作业名后括号里是调用深度,数字越大越可能是同名函数误连。完整明细见 tables.csv。",
        "",
        "## 按更新频率",
        tbl(["频率", "表数"], [(k, by_freq[k]) for k in sorted(by_freq, key=lambda x: x if x not in FREQ_ORDER else f"{FREQ_ORDER.index(x):02d}")]),
        "",
        f"## 超过 SLA 的表({len(stale)})",
        tbl(["库.表", "频率", "最近", "滞后", "写入作业"], [(f"{r['db']}.{r['table']}", r["frequency"], r["last"], r["lag"], r["jobs"]) for r in stale]),
        "",
        f"## 多处写入的表(≥3 个文件,{len(multi)}):D8 单写者队列优先收口",
        tbl(["库.表", "写入文件数", "作业"], [(f"{r['db']}.{r['table']}", r["writer_count"], r["jobs"]) for r in multi]),
        "",
        f"## 没有读取方的表({len(no_reader)}):P4 处置候选(删除前人工复核动态 SQL)",
        tbl(["库.表", "行数", "MB", "最近"], [(f"{r['db']}.{r['table']}", r["rows"], r["mb"], r["last"]) for r in no_reader]),
        "",
        f"## 没有写入方的表({len(no_writer)}):可能是历史遗留或动态表名",
        tbl(["库.表", "行数", "MB", "最近"], [(f"{r['db']}.{r['table']}", r["rows"], r["mb"], r["last"]) for r in no_writer]),
        "",
        f"## 有读取方但没对应到任何页面的表({len(no_page)}):内部 / 调度用,或前端用了动态路径",
        tbl(["库.表", "读取函数数", "API"], [(f"{r['db']}.{r['table']}", r["reader_count"], r["api_reads"][:80]) for r in no_page]),
        "",
        "## 磁盘占用前 15",
        tbl(["库.表", "MB", "行数"], [(f"{r['db']}.{r['table']}", r["mb"], r["rows"]) for r in biggest]),
        "",
        "## 数据源 → 受影响的表和页面",
        tbl(["数据源", "表数", "页面"], [(s, len(v["tables"]), "、".join(sorted(v["pages"]))[:120]) for s, v in sorted(src_view.items())]),
        "",
        f"## 动态表名的 SQL({len(dyn)} 处,扫不到,需人工补齐):见 dynamic_sql.csv",
    ]
    open(path, "w", encoding="utf-8").write("\n".join(md) + "\n")


if __name__ == "__main__":
    sys.exit(main())
