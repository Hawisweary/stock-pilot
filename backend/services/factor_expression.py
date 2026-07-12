"""因子表达式引擎 — 安全解析 + OHLCV 时序求值"""
from __future__ import annotations

import ast
import json
import math
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from config import DB_PATH

FIELD_MAP = {
    "$close": "close",
    "$adj_close": "adj_close",
    "$open": "open",
    "$high": "high",
    "$low": "low",
    "$volume": "volume",
}

ALLOWED_FUNCS = frozenset({"Ref", "Mean", "Std", "Delta", "Rank", "Abs", "Log"})


class ExpressionError(ValueError):
    pass


def _safe_eval_scalar(expr: str, env: Dict[str, float]) -> float:
    """F001-F015 线性组合（兼容 custom_factor）。"""
    tree = ast.parse(expr, mode="eval")
    return float(_eval_ast(tree.body, env))


def _eval_ast(node: ast.AST, env: Dict[str, float]) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.Name):
        key = node.id.upper()
        if key not in env:
            raise ExpressionError(f"未知变量 {node.id}")
        return float(env[key])
    if isinstance(node, ast.BinOp):
        a, b = _eval_ast(node.left, env), _eval_ast(node.right, env)
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Mult):
            return a * b
        if isinstance(node.op, ast.Div):
            return a / b if abs(b) > 1e-12 else 0.0
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_eval_ast(node.operand, env)
    raise ExpressionError("不支持的表达式节点")


def validate_expression(formula: str) -> dict:
    formula = formula.strip()
    if not formula:
        return {"valid": False, "error": "空公式"}
    if formula.startswith("$") or any(fn + "(" in formula for fn in ALLOWED_FUNCS):
        try:
            _parse_ts_formula(formula)
            return {"valid": True, "kind": "timeseries", "formula": formula}
        except ExpressionError as e:
            return {"valid": False, "error": str(e)}
    try:
        env = {f"F{i:03d}": 1.0 for i in range(1, 16)}
        _safe_eval_scalar(formula.upper(), env)
        return {"valid": True, "kind": "cross_section", "formula": formula.upper()}
    except Exception as e:
        return {"valid": False, "error": str(e)}


def _tokenize(formula: str) -> List[str]:
    pattern = re.compile(
        r"\$[a-z_]+|[A-Za-z_][A-Za-z0-9_]*|\d+\.?\d*|[+\-*/(),]"
    )
    return pattern.findall(formula.replace(" ", ""))


@dataclass
class Node:
    kind: str
    value: Any = None
    children: Tuple["Node", ...] = ()


def _parse_ts_formula(formula: str) -> Node:
    tokens = _tokenize(formula)
    pos = 0

    def peek() -> Optional[str]:
        return tokens[pos] if pos < len(tokens) else None

    def consume(expected: Optional[str] = None) -> str:
        nonlocal pos
        if pos >= len(tokens):
            raise ExpressionError("表达式不完整")
        tok = tokens[pos]
        if expected and tok != expected:
            raise ExpressionError(f"期望 {expected}，得到 {tok}")
        pos += 1
        return tok

    def parse_expr() -> Node:
        node = parse_term()
        while peek() in ("+", "-"):
            op = consume()
            right = parse_term()
            node = Node("binop", op, (node, right))
        return node

    def parse_term() -> Node:
        node = parse_factor()
        while peek() in ("*", "/"):
            op = consume()
            right = parse_factor()
            node = Node("binop", op, (node, right))
        return node

    def parse_factor() -> Node:
        tok = peek()
        if tok is None:
            raise ExpressionError("表达式不完整")
        if tok in ("+", "-"):
            op = consume()
            return Node("unop", op, (parse_factor(),))
        if tok == "(":
            consume("(")
            node = parse_expr()
            consume(")")
            return node
        if tok in FIELD_MAP or tok.startswith("$"):
            if tok not in FIELD_MAP:
                raise ExpressionError(f"未知字段 {tok}")
            consume()
            return Node("field", tok)
        if re.match(r"^\d+\.?\d*$", tok):
            consume()
            return Node("number", float(tok))
        if tok[0].isalpha():
            name = consume()
            if peek() == "(":
                consume("(")
                args = []
                if peek() != ")":
                    args.append(parse_expr())
                    while peek() == ",":
                        consume(",")
                        args.append(parse_expr())
                consume(")")
                if name not in ALLOWED_FUNCS:
                    raise ExpressionError(f"未知函数 {name}")
                return Node("call", name, tuple(args))
            raise ExpressionError(f"未知标识符 {name}")
        raise ExpressionError(f"无法解析 {tok}")

    root = parse_expr()
    if pos != len(tokens):
        raise ExpressionError("表达式有多余 token")
    return root


def _align_series(a: List[float], b: List[float]) -> Tuple[List[float], List[float]]:
    n = min(len(a), len(b))
    return a[:n], b[:n]


def _rolling_mean(s: List[float], n: int) -> List[float]:
    out: List[float] = []
    for i in range(len(s)):
        window = s[max(0, i - n + 1) : i + 1]
        out.append(sum(window) / len(window) if window else 0.0)
    return out


def _rolling_std(s: List[float], n: int) -> List[float]:
    out: List[float] = []
    for i in range(len(s)):
        window = s[max(0, i - n + 1) : i + 1]
        if len(window) < 2:
            out.append(0.0)
            continue
        m = sum(window) / len(window)
        var = sum((x - m) ** 2 for x in window) / len(window)
        out.append(math.sqrt(var))
    return out


def _ref(s: List[float], n: int) -> List[float]:
    out: List[float] = []
    for i in range(len(s)):
        j = i + n
        out.append(s[j] if j < len(s) else s[-1] if s else 0.0)
    return out


def _eval_node(node: Node, ctx: Dict[str, List[float]]) -> List[float]:
    if node.kind == "number":
        length = len(next(iter(ctx.values()), [])) or 1
        return [float(node.value)] * length
    if node.kind == "field":
        col = FIELD_MAP[str(node.value)]
        if col not in ctx:
            raise ExpressionError(f"缺少字段 {node.value}")
        return ctx[col][:]
    if node.kind == "unop":
        s = _eval_node(node.children[0], ctx)
        return [-x for x in s] if node.value == "-" else s
    if node.kind == "binop":
        a, b = _eval_node(node.children[0], ctx), _eval_node(node.children[1], ctx)
        a, b = _align_series(a, b)
        if node.value == "+":
            return [x + y for x, y in zip(a, b)]
        if node.value == "-":
            return [x - y for x, y in zip(a, b)]
        if node.value == "*":
            return [x * y for x, y in zip(a, b)]
        return [x / y if abs(y) > 1e-12 else 0.0 for x, y in zip(a, b)]
    if node.kind == "call":
        fn = str(node.value)
        if fn == "Ref":
            s = _eval_node(node.children[0], ctx)
            n = int(_eval_node(node.children[1], ctx)[0]) if len(node.children) > 1 else 1
            return _ref(s, n)
        if fn == "Mean":
            s = _eval_node(node.children[0], ctx)
            n = int(_eval_node(node.children[1], ctx)[0]) if len(node.children) > 1 else 5
            return _rolling_mean(s, max(1, n))
        if fn == "Std":
            s = _eval_node(node.children[0], ctx)
            n = int(_eval_node(node.children[1], ctx)[0]) if len(node.children) > 1 else 5
            return _rolling_std(s, max(2, n))
        if fn == "Delta":
            s = _eval_node(node.children[0], ctx)
            n = int(_eval_node(node.children[1], ctx)[0]) if len(node.children) > 1 else 1
            ref = _ref(s, n)
            a, b = _align_series(s, ref)
            return [x - y for x, y in zip(a, b)]
        if fn == "Abs":
            return [abs(x) for x in _eval_node(node.children[0], ctx)]
        if fn == "Log":
            return [math.log(abs(x) + 1e-9) for x in _eval_node(node.children[0], ctx)]
        if fn == "Rank":
            return _eval_node(node.children[0], ctx)
    raise ExpressionError(f"未知节点 {node.kind}")


def _load_quote_panel(conn: sqlite3.Connection, stock_id: int, max_days: int) -> Dict[str, List[float]]:
    rows = conn.execute(
        """SELECT trade_date, open, high, low,
                  COALESCE(adj_close, close) AS adj_close, close, volume
           FROM stock_daily_quotes
           WHERE stock_id=? AND COALESCE(adj_close, close) IS NOT NULL
           ORDER BY trade_date DESC LIMIT ?""",
        (stock_id, max_days),
    ).fetchall()
    if not rows:
        return {}
    rows = list(reversed(rows))
    ctx: Dict[str, List[float]] = {
        "open": [float(r[1] or 0) for r in rows],
        "high": [float(r[2] or 0) for r in rows],
        "low": [float(r[3] or 0) for r in rows],
        "adj_close": [float(r[4] or 0) for r in rows],
        "close": [float(r[5] or r[4] or 0) for r in rows],
        "volume": [float(r[6] or 0) for r in rows],
    }
    return ctx


def _cross_section_rank(values: Dict[int, float]) -> Dict[int, float]:
    if not values:
        return {}
    sorted_items = sorted(values.items(), key=lambda x: x[1])
    n = len(sorted_items)
    return {sid: round(100.0 * (i + 1) / n, 2) for i, (sid, _) in enumerate(sorted_items)}


def compute_expression(
    formula: str,
    name: str,
    *,
    factor_id: Optional[str] = None,
    max_days: int = 60,
) -> dict:
    """计算时序表达式因子并写入 factor_values。"""
    from services.custom_factor import _eval_formula, _load_factor_env
    from services.factor_factory import _upsert_factor, init_factor_store

    v = validate_expression(formula)
    if not v.get("valid"):
        return {"error": v.get("error", "invalid")}

    conn = init_factor_store()
    if v.get("kind") == "cross_section":
        from services.custom_factor import init_custom_factor_tables

        init_custom_factor_tables(conn)
        fid = factor_id or _allocate_expr_id(conn)
        calc_date = conn.execute("SELECT MAX(calc_date) FROM comprehensive_scores").fetchone()[0]
        if not calc_date:
            conn.close()
            return {"error": "无评分数据"}
        count = 0
        for (sid,) in conn.execute("SELECT id FROM stocks WHERE is_active=1"):
            env = _load_factor_env(conn, sid, calc_date)
            val = _eval_formula(v["formula"], env)
            if val is not None:
                _upsert_factor(conn, sid, calc_date, fid, val)
                count += 1
        conn.execute(
            "INSERT OR REPLACE INTO factor_expressions (factor_id, name, formula, kind) VALUES (?,?,?,?)",
            (fid, name, v["formula"], "cross_section"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO factor_registry (factor_id, name, category, formula) VALUES (?,?,?,?)",
            (fid, name, "表达式", v["formula"]),
        )
        conn.commit()
        conn.close()
        return {"factor_id": fid, "kind": "cross_section", "computed": count, "date": calc_date}

    root = _parse_ts_formula(formula)
    stocks = conn.execute("SELECT id FROM stocks WHERE is_active=1").fetchall()
    latest_vals: Dict[int, float] = {}
    latest_date = conn.execute("SELECT MAX(trade_date) FROM stock_daily_quotes").fetchone()[0]
    for (sid,) in stocks:
        ctx = _load_quote_panel(conn, sid, max_days)
        if not ctx:
            continue
        series = _eval_node(root, ctx)
        if "Rank(" in formula:
            latest_vals[sid] = series[-1]
        else:
            latest_vals[sid] = series[-1]

    if "Rank(" in formula:
        latest_vals = _cross_section_rank(latest_vals)

    fid = factor_id or _allocate_expr_id(conn)
    dt = latest_date or __import__("datetime").date.today().isoformat()
    for sid, val in latest_vals.items():
        if val is not None and not math.isnan(val):
            _upsert_factor(conn, sid, dt, fid, float(val))

    conn.execute(
        "INSERT OR REPLACE INTO factor_expressions (factor_id, name, formula, kind) VALUES (?,?,?,?)",
        (fid, name, formula, "timeseries"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO factor_registry (factor_id, name, category, formula) VALUES (?,?,?,?)",
        (fid, name, "表达式", formula),
    )
    conn.commit()
    conn.close()
    return {
        "factor_id": fid,
        "kind": "timeseries",
        "computed": len(latest_vals),
        "date": dt,
    }


def _allocate_expr_id(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(factor_id,2) AS INTEGER)) FROM factor_registry WHERE factor_id LIKE 'F%'"
    ).fetchone()
    n = (row[0] or 15) + 1
    return f"F{n:03d}"


def ensure_expression_tables(conn: Optional[sqlite3.Connection] = None) -> None:
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS factor_expressions (
            factor_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            formula TEXT NOT NULL,
            kind TEXT DEFAULT 'timeseries',
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    if own:
        conn.close()


def list_expressions() -> List[dict]:
    ensure_expression_tables()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM factor_expressions ORDER BY created_at DESC").fetchall()]
    conn.close()
    return rows
