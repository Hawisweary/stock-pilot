"""test_profile_isolation.py — M1-4 隔离硬门禁单测
断言 portfolio_svc / score_alerts / select_top_n SQL 不含 stock_score_profiles。
运行：cd backend && python -m pytest tests/test_profile_isolation.py -v
"""
import ast
import inspect
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FORBIDDEN = "stock_score_profiles"


def _read_source(module_path: str) -> str:
    full = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), module_path)
    with open(full) as f:
        return f.read()


def _sql_strings(source: str) -> list[str]:
    """提取源码中所有字符串字面量（SQL 可能藏在其中）。"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    results = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            results.append(node.value)
    return results


def test_portfolio_svc_no_profile_table():
    src = _read_source("services/portfolio_svc.py")
    for s in _sql_strings(src):
        assert FORBIDDEN not in s, (
            f"portfolio_svc.py SQL 含 {FORBIDDEN}，违反 M1 隔离红线：{s[:120]}"
        )


def test_strategy_selector_no_profile_table():
    src = _read_source("services/strategy_selector.py")
    for s in _sql_strings(src):
        assert FORBIDDEN not in s, (
            f"strategy_selector.py SQL 含 {FORBIDDEN}，违反 M1 隔离红线：{s[:120]}"
        )


def test_score_alerts_function_no_profile_table():
    src = _read_source("services/portfolio_svc.py")
    # 定位 score_alerts 函数体
    match = re.search(r"def score_alerts\(.*?(?=\ndef |\Z)", src, re.DOTALL)
    if match:
        func_src = match.group()
        for s in _sql_strings(func_src):
            assert FORBIDDEN not in s, (
                f"score_alerts SQL 含 {FORBIDDEN}，违反 M1 隔离红线：{s[:120]}"
            )


def test_v5_ranking_default_no_profile():
    """scores/ranking 端点默认路径不引用 stock_score_profiles。"""
    src = _read_source("api/scores.py")
    # 找到 score_ranking 函数（不包含 profile 路由的部分）
    match = re.search(r"async def score_ranking\(.*?(?=\n@router|\nasync def |\Z)", src, re.DOTALL)
    if match:
        func_src = match.group()
        assert FORBIDDEN not in func_src, (
            f"score_ranking 含 {FORBIDDEN}，默认排名不得读 profile 表"
        )


def test_profile_table_only_in_allowed_files():
    """stock_score_profiles 只允许出现在 v5_scorer.py 和 scores.py 的 profile 路由里。"""
    allowed = {
        "services/v5_scorer.py",
        "api/scores.py",
        "scripts/audit_profile_distribution.py",
        "tests/test_profile_isolation.py",
    }
    check_files = [
        "services/portfolio_svc.py",
        "services/strategy_selector.py",
        "services/backtest_engine.py",
        "services/score_alert.py",
    ]
    for rel in check_files:
        full = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), rel)
        if not os.path.exists(full):
            continue
        with open(full) as f:
            content = f.read()
        assert FORBIDDEN not in content, (
            f"{rel} 含 {FORBIDDEN}，违反 M1 隔离红线"
        )
