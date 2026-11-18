"""数据基线(P6)与 API 契约对照(R1)共用的静态索引。

只读:扫描 backend/*.py 与 frontend/**/*.ts(x) 源码,不连接数据库、不发网络请求。

- 后端索引:每个函数里出现的 SQL(写入表 / 读取表)、调用了哪些函数名、
  引用了哪些数据源适配器;FastAPI 路由(方法 + 完整路径 → 处理函数);
  调度作业(run_scheduled_job.py 的各分支 → 调用的函数和子进程脚本)。
- 前端索引:每个文件里出现的 API 路径;lib/api.ts 中 api.xxx → 路径;
  文件间 import 关系 → 每个文件被哪些页面路由用到。

调用图按"函数名"近似(同名函数都算可达),结论是"可能写入 / 可能读取",
用于盘点和排查,不是精确的数据流分析。
"""
from __future__ import annotations

import ast
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_DIR = os.path.dirname(BACKEND_DIR)
FRONTEND_DIR = os.path.join(PROJECT_DIR, "frontend")

SKIP_DIRS = {"__pycache__", "legacy", "tests", "data", "logs", ".pytest_cache", "research"}

# ── SQL 识别 ──────────────────────────────────────────────
_RE_WRITE = re.compile(
    r"\b(INSERT\s+(?:OR\s+\w+\s+)?INTO|REPLACE\s+INTO|UPDATE|DELETE\s+FROM)\s+[\"`\[]?(\w+)",
    re.I,
)
_RE_READ = re.compile(r"(\bDELETE\s+)?\b(FROM|JOIN)\s+[\"`\[]?(\w+)", re.I)
_RE_TO_SQL = re.compile(r"\.to_sql\(\s*[\"'](\w+)[\"']")
_RE_SQL_HINT = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|REPLACE)\b", re.I)
_RE_DYNAMIC = re.compile(r"\b(INSERT\s+(?:OR\s+\w+\s+)?INTO|REPLACE\s+INTO|UPDATE|DELETE\s+FROM|FROM|JOIN)\s+\{", re.I)

# 数据源适配器:按模块名 / 关键字识别(函数体或其所在文件的 import)
SOURCE_PATTERNS = {
    "tushare": re.compile(r"\btushare|\btushare_\w+|\bts\.pro_api|\bpro\.\w+\(", re.I),
    "akshare": re.compile(r"\bakshare\b|\bak\.\w+\(", re.I),
    "yfinance": re.compile(r"\byfinance\b|\byf\.(Ticker|download)\b"),
    "tiingo": re.compile(r"\btiingo", re.I),
    "adata": re.compile(r"\badata(_adapter)?\b"),
    "baostock": re.compile(r"\bbaostock\b|\bbs\.query", re.I),
    "eastmoney": re.compile(r"eastmoney|dfcfw|push2\.eastmoney", re.I),
    "sina": re.compile(r"sinajs|finance\.sina", re.I),
    "sec_edgar": re.compile(r"sec\.gov|edgar", re.I),
    "fred": re.compile(r"\bfred\b|stlouisfed", re.I),
    "llm": re.compile(r"\bllm_client\b|deepseek|anthropic|openai", re.I),
    "data_sources": re.compile(r"\bdata_sources\b|\bastock_data\b|\bus_market_adapter\b"),
}

# 调用图里太通用、会把一切连起来的名字
GENERIC_CALLS = {
    "get", "set", "run", "main", "execute", "executemany", "fetchone", "fetchall", "commit", "close",
    "connect", "append", "extend", "update", "items", "keys", "values", "format", "join", "split",
    "strip", "lower", "upper", "replace", "startswith", "endswith", "load", "loads", "dump", "dumps",
    "read", "write", "open", "print", "len", "str", "int", "float", "dict", "list", "tuple", "sorted",
    "min", "max", "sum", "round", "range", "enumerate", "zip", "isinstance", "getattr", "setattr",
    "info", "warning", "error", "debug", "exception", "now", "today", "strftime", "strptime",
    "submit", "result", "start", "stop", "sleep", "add", "pop", "copy", "apply", "map", "filter",
    "to_dict", "to_sql", "read_sql", "read_sql_query", "DataFrame", "Series", "cursor", "rollback",
    "init", "setup", "process", "handle", "build", "compute", "calc", "fetch", "save", "sync",
}
MAX_DEFS_FOR_NAME = 4  # 同名定义超过这个数的函数名不参与调用图(太容易误连)


@dataclass
class FuncInfo:
    module: str           # 相对 backend 的路径,如 services/scheduler.py
    qualname: str         # 函数名(类方法带类名);模块级代码记为 <module>
    lineno: int
    writes: set = field(default_factory=set)
    reads: set = field(default_factory=set)
    calls: set = field(default_factory=set)
    scripts: set = field(default_factory=set)   # 以字符串形式出现的 xxx.py(子进程脚本)
    sources: set = field(default_factory=set)
    dynamic_sql: list = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.module}:{self.qualname}"


@dataclass
class Route:
    method: str
    path: str
    module: str
    func: str

    @property
    def func_key(self) -> str:
        return f"{self.module}:{self.func}"


def _iter_py_files(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def _strings_of(node: ast.AST) -> list[str]:
    """节点里的字符串常量;f-string 的插值部分用 {} 占位,便于识别动态表名。"""
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.append(n.value)
        elif isinstance(n, ast.JoinedStr):
            parts = []
            for v in n.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    parts.append(v.value)
                else:
                    parts.append("{}")
            out.append("".join(parts))
    return out


def extract_sql_tables(text: str) -> tuple[set, set]:
    """返回 (写入表, 读取表);未按真实表名过滤。"""
    writes, reads = set(), set()
    if not _RE_SQL_HINT.search(text) and ".to_sql(" not in text:
        return writes, reads
    for m in _RE_WRITE.finditer(text):
        writes.add(m.group(2))
    for m in _RE_TO_SQL.finditer(text):
        writes.add(m.group(1))
    for m in _RE_READ.finditer(text):
        if m.group(1):  # DELETE FROM 是写,不算读
            continue
        reads.add(m.group(3))
    return writes, reads


class _FuncVisitor(ast.NodeVisitor):
    """把语句归到最内层的函数;模块级语句归到 <module>。"""

    def __init__(self, module: str, source: str):
        self.module = module
        self.source = source
        self.funcs: dict[str, FuncInfo] = {}
        self.stack: list[str] = []
        self.class_stack: list[str] = []
        self._cur("<module>", 1)

    def _cur(self, qual: str, lineno: int) -> FuncInfo:
        if qual not in self.funcs:
            self.funcs[qual] = FuncInfo(self.module, qual, lineno)
        return self.funcs[qual]

    def _current(self) -> FuncInfo:
        return self.funcs[self.stack[-1]] if self.stack else self.funcs["<module>"]

    def visit_ClassDef(self, node):
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def _visit_func(self, node):
        qual = ".".join(self.class_stack + [node.name]) if self.class_stack and not self.stack else node.name
        if self.stack:  # 嵌套函数:并入外层,避免丢失归属
            qual = self.stack[-1]
            self.generic_visit(node)
            return
        self._cur(qual, node.lineno)
        self.stack.append(qual)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = _visit_func
    visit_AsyncFunctionDef = _visit_func

    def visit_Call(self, node):
        f = self._current()
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else None
        if name:
            f.calls.add(name)
        self.generic_visit(node)

    def visit_Constant(self, node):
        if isinstance(node.value, str):
            self._handle_string(node.value, node.lineno)

    def visit_JoinedStr(self, node):
        for s in _strings_of(node):
            self._handle_string(s, node.lineno)

    def _handle_string(self, s: str, lineno: int):
        f = self._current()
        w, r = extract_sql_tables(s)
        f.writes |= w
        f.reads |= r
        if _RE_DYNAMIC.search(s):
            f.dynamic_sql.append((lineno, " ".join(s.split())[:120]))
        if s.endswith(".py") and "/" not in s and len(s) < 80:
            f.scripts.add(s)


@dataclass
class BackendIndex:
    funcs: dict = field(default_factory=dict)            # key -> FuncInfo
    by_name: dict = field(default_factory=lambda: defaultdict(list))  # 函数名 -> [key]
    routes: list = field(default_factory=list)
    jobs: dict = field(default_factory=dict)             # 作业名 -> set(入口 key)
    module_sources: dict = field(default_factory=dict)   # module -> set(source)
    script_entry: dict = field(default_factory=dict)     # "xxx.py" -> key of <module>


_ROUTE_METHODS = {"get", "post", "put", "delete", "patch"}


def build_backend_index(root: str = BACKEND_DIR) -> BackendIndex:
    idx = BackendIndex()
    for path in _iter_py_files(root):
        module = os.path.relpath(path, root)
        try:
            src = open(path, encoding="utf-8").read()
            tree = ast.parse(src)
        except (SyntaxError, UnicodeDecodeError):
            continue
        v = _FuncVisitor(module, src)
        v.visit(tree)
        msrc = {name for name, pat in SOURCE_PATTERNS.items() if pat.search(src)}
        idx.module_sources[module] = msrc
        # 函数级数据源:函数体文本命中,否则继承所在文件
        lines = src.splitlines()
        func_nodes = {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for qual, fi in v.funcs.items():
            base = qual.split(".")[-1]
            node = func_nodes.get(base)
            if node is not None and qual != "<module>":
                body = "\n".join(lines[node.lineno - 1: getattr(node, "end_lineno", node.lineno)])
                fi.sources = {name for name, pat in SOURCE_PATTERNS.items() if pat.search(body)}
            idx.funcs[fi.key] = fi
            idx.by_name[base].append(fi.key)
        idx.script_entry[os.path.basename(module)] = f"{module}:<module>"
        # 路由
        prefix = ""
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call):
                callee = n.value.func
                if (isinstance(callee, ast.Name) and callee.id == "APIRouter") or (
                    isinstance(callee, ast.Attribute) and callee.attr == "APIRouter"
                ):
                    for kw in n.value.keywords:
                        if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                            prefix = kw.value.value
        for n in ast.walk(tree):
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in n.decorator_list:
                if (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr in _ROUTE_METHODS
                    and dec.args
                    and isinstance(dec.args[0], ast.Constant)
                    and isinstance(dec.args[0].value, str)
                ):
                    owner = dec.func.value
                    owner_name = owner.id if isinstance(owner, ast.Name) else ""
                    p = dec.args[0].value
                    full = (prefix + p) if owner_name == "router" else p
                    idx.routes.append(Route(dec.func.attr.upper(), full or "/", module, n.name))
    idx.jobs = _parse_jobs(idx, root)
    return idx


def _parse_jobs(idx: BackendIndex, root: str) -> dict:
    """run_scheduled_job.py 的 if/elif 分支 → 入口函数 key 集合。"""
    path = os.path.join(root, "scripts", "run_scheduled_job.py")
    jobs: dict[str, set] = {}
    if not os.path.exists(path):
        return jobs
    tree = ast.parse(open(path, encoding="utf-8").read())

    def entries_of(stmts) -> set:
        keys = set()
        for st in stmts:
            for n in ast.walk(st):
                if isinstance(n, ast.Call):
                    fn = n.func
                    name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else None
                    if name and name in idx.by_name and len(idx.by_name[name]) <= MAX_DEFS_FOR_NAME and name not in GENERIC_CALLS:
                        keys |= set(idx.by_name[name])
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.endswith(".py"):
                    if n.value in idx.script_entry:
                        keys.add(idx.script_entry[n.value])
        return keys

    def walk_if(node: ast.If):
        t = node.test
        if (
            isinstance(t, ast.Compare)
            and isinstance(t.left, ast.Attribute)
            and t.left.attr == "job"
            and t.comparators
            and isinstance(t.comparators[0], ast.Constant)
        ):
            jobs[t.comparators[0].value] = entries_of(node.body)
            if node.orelse:
                if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
                    walk_if(node.orelse[0])
                else:
                    jobs["daily"] = entries_of(node.orelse)

    for n in ast.walk(tree):
        if isinstance(n, ast.If):
            walk_if(n)
            if jobs:
                break
    # 调度器在 weekly 之后直接拉起的脚本
    if "weekly" in jobs and "sync_industry_batch.py" in idx.script_entry:
        jobs["weekly"].add(idx.script_entry["sync_industry_batch.py"])
    return jobs


def reachable_depth(idx: BackendIndex, entry_keys, max_depth: int = 8) -> dict:
    """从入口函数出发,按函数名近似的调用图可达的函数 → 最短调用深度(入口为 0)。
    深度越大越可能是误连(同名函数),报告里一并给出,便于判断。"""
    dist = {k: 0 for k in entry_keys if k in idx.funcs}
    frontier = list(dist)
    depth = 0
    while frontier and depth < max_depth:
        depth += 1
        nxt = []

        def visit(k2):
            if k2 not in dist:
                dist[k2] = depth
                nxt.append(k2)

        for k in frontier:
            fi = idx.funcs[k]
            for name in fi.calls:
                if name in GENERIC_CALLS:
                    continue
                defs = idx.by_name.get(name)
                if not defs or len(defs) > MAX_DEFS_FOR_NAME:
                    continue
                for d in defs:
                    visit(d)
            for sc in fi.scripts:
                e = idx.script_entry.get(sc)
                if e:
                    visit(e)
            # 模块级代码会调用本文件的函数(脚本的 main 等)
            if fi.qualname == "<module>":
                for k2, f2 in idx.funcs.items():
                    if f2.module == fi.module and f2.qualname.split(".")[-1] in fi.calls:
                        visit(k2)
        frontier = nxt
    return dist


def reachable(idx: BackendIndex, entry_keys, max_depth: int = 8) -> set:
    return set(reachable_depth(idx, entry_keys, max_depth))


# ── 前端索引 ──────────────────────────────────────────────
_RE_FE_PATH = re.compile(r"[\"'`](/api/[^\"'`\s]*)[\"'`]")
_RE_API_KEY = re.compile(r"^\s{2}(\w+)\s*:", re.M)
_RE_REQ_PATH = re.compile(r"request\s*(?:<[^()]*?>)?\s*\(\s*[\"'`](/[^\"'`]*)[\"'`]")
_RE_METHOD = re.compile(r"method\s*:\s*[\"'](GET|POST|PUT|DELETE|PATCH)[\"']")
_RE_IMPORT = re.compile(r"""from\s+["']([^"']+)["']""")
_RE_API_USE = re.compile(r"\bapi\.(\w+)\s*\(")


@dataclass
class FrontendIndex:
    file_paths: dict = field(default_factory=lambda: defaultdict(set))   # 文件 -> {(method, path)}
    api_fn: dict = field(default_factory=dict)                           # api.xxx -> [(method, path)]
    pages_of: dict = field(default_factory=dict)                          # 文件 -> {路由}
    routes: list = field(default_factory=list)


def _norm_fe(p: str) -> str:
    p = p.split("?")[0]
    p = re.sub(r"\$\{[^}]*\}", "{}", p)
    # 段尾拼接的变量多半是查询串(如 `/macro/indicators${qs}`),去掉
    p = "/".join(re.sub(r"(?<=[\w-])\{\}$", "", seg) for seg in p.split("/"))
    return p.rstrip("/") or "/"


def _method_after(src: str, pos: int) -> str:
    """路径字面量之后、下一个请求调用之前,找 method: "XXX";找不到按 GET。"""
    tail = src[pos: pos + 260]
    # 截到下一个路径字面量 / 下一个函数 / 空行为止,避免把后面另一次调用的 method 算进来
    cut = re.search(r"[\"'`](/|\$\{API_BASE\})|\bfunction\b|\n\s*\n|\b(request|fetch)\s*[<(]", tail)
    if cut:
        tail = tail[: cut.start()]
    m = _RE_METHOD.search(tail)
    return m.group(1) if m else "GET"


def build_frontend_index(root: str = FRONTEND_DIR) -> FrontendIndex:
    fi = FrontendIndex()
    files = []
    for sub in ("app", "components", "lib"):
        base = os.path.join(root, sub)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in {"node_modules", ".next"}]
            for fn in filenames:
                if fn.endswith((".ts", ".tsx")):
                    files.append(os.path.join(dirpath, fn))
    text = {f: open(f, encoding="utf-8").read() for f in files}
    rel = {f: os.path.relpath(f, root) for f in files}

    # lib/api.ts:api 对象的每个键 → 请求路径(request 自动加 /api 前缀)
    api_ts = os.path.join(root, "lib", "api.ts")
    if api_ts in text:
        src = text[api_ts]
        start = src.find("export const api = {")
        end = src.find("\n};", start)
        body = src[start:end]
        keys = list(_RE_API_KEY.finditer(body))
        for i, m in enumerate(keys):
            chunk = body[m.end(): keys[i + 1].start() if i + 1 < len(keys) else len(body)]
            reqs = []
            for rm in _RE_REQ_PATH.finditer(chunk):
                reqs.append((_method_after(chunk, rm.end()), _norm_fe("/api" + rm.group(1))))
            if reqs:
                fi.api_fn[m.group(1)] = reqs
        # api.ts 里 api 对象之外的独立函数(轮询等)
        outside = src[:start] + src[end:]
        for rm in _RE_REQ_PATH.finditer(outside):
            fi.file_paths[rel[api_ts]].add((_method_after(outside, rm.end()), _norm_fe("/api" + rm.group(1))))
    market_ts = os.path.join(root, "lib", "market.ts")
    if market_ts in text:
        for m in re.finditer(r"`\$\{API_BASE\}(/?[^`]*)`", text[market_ts]):
            fi.file_paths[rel[market_ts]].add((_method_after(text[market_ts], m.end()), _norm_fe("/api/market" + m.group(1))))

    for f, src in text.items():
        for m in _RE_FE_PATH.finditer(src):
            if f == market_ts and m.group(1) == "/api/market":
                continue  # 这是 API_BASE 常量本身,不是一次调用
            fi.file_paths[rel[f]].add((_method_after(src, m.end()), _norm_fe(m.group(1))))
        if f != api_ts:
            for m in _RE_API_USE.finditer(src):
                for mp in fi.api_fn.get(m.group(1), []):
                    fi.file_paths[rel[f]].add(mp)

    # import 图 → 页面
    def resolve(frm: str, spec: str):
        if spec.startswith("@/"):
            cand = os.path.join(root, spec[2:])
        elif spec.startswith("."):
            cand = os.path.normpath(os.path.join(os.path.dirname(frm), spec))
        else:
            return None
        for ext in ("", ".tsx", ".ts", "/index.tsx", "/index.ts"):
            if cand + ext in text:
                return cand + ext
        return None

    imports = {f: {r for r in (resolve(f, s) for s in _RE_IMPORT.findall(src)) if r} for f, src in text.items()}
    pages = [f for f in files if os.path.basename(f) == "page.tsx" and rel[f].startswith("app" + os.sep)]
    for p in pages:
        route = "/" + os.path.dirname(rel[p])[len("app"):].strip(os.sep).replace(os.sep, "/")
        route = route.rstrip("/") or "/"
        fi.routes.append(route)
        stack, seen = [p], set()
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            fi.pages_of.setdefault(rel[cur], set()).add(route)
            stack.extend(imports.get(cur, ()))
    return fi


def norm_backend_path(p: str) -> str:
    return re.sub(r"\{[^}]*\}", "{}", p).rstrip("/") or "/"


def match_score(backend: str, frontend: str) -> int:
    """匹配得分(越高越像同一个接口);-1 表示不匹配。
    字面段相同 / 两边都是变量:2;前端字面量对后端变量:1;前端变量对后端字面量:0。"""
    sa, sb = backend.strip("/").split("/"), frontend.strip("/").split("/")
    if len(sa) != len(sb):
        return -1
    score = 0
    for b, f in zip(sa, sb):
        if b == f:
            score += 2
        elif b == "{}":
            score += 1
        elif f == "{}":
            score += 0
        else:
            return -1
    return score


def path_match(a: str, b: str) -> bool:
    """两个规范化路径是否匹配:段数相同,{} 可匹配任意段。"""
    sa, sb = a.strip("/").split("/"), b.strip("/").split("/")
    if len(sa) != len(sb):
        return False
    return all(x == y or x == "{}" or y == "{}" for x, y in zip(sa, sb))
