"""A 股市场 / 板块筛选 SQL 片段（统一 scope，避免 market='A' 只命中少量 legacy 行）。"""
from __future__ import annotations

MARKET_SCOPES: dict[str, str] = {
    "ALL": "全部",
    "A": "全部 A 股",
    "SH": "沪市",
    "SZ": "深市",
    "STAR": "科创板",
    "CHINEXT": "创业板",
    "MAIN_SH": "沪市主板",
    "MAIN_SZ": "深市主板",
    "SME": "中小板",
    "BJ": "北交所",
}


def normalize_scope(scope: str | None = None, *, market: str | None = None) -> str:
    """scope 优先；兼容旧 market 参数。"""
    raw = (scope or market or "A").upper().strip()
    if raw in ("", "ALL"):
        return "ALL"
    if raw in MARKET_SCOPES:
        return raw
    return "A"


def scope_label(scope: str) -> str:
    return MARKET_SCOPES.get(scope, scope)


def scope_sql(
    scope: str,
    *,
    market_col: str = "market",
    code_col: str = "code",
) -> tuple[str, list]:
    """返回 (WHERE 子句片段, 参数)。空 scope/ALL 时不追加条件。"""
    s = normalize_scope(scope)
    if s == "ALL":
        return "", []

    m, c = market_col, code_col

    if s == "A":
        clause = (
            f"({m} IN ('A','SH','SZ') OR "
            f"({c} LIKE '0%' OR {c} LIKE '3%' OR {c} LIKE '6%' OR {c} LIKE '92%'))"
        )
    elif s == "SH":
        clause = f"({m}='SH' OR ({m}='A' AND {c} LIKE '6%'))"
    elif s == "SZ":
        clause = (
            f"({m}='SZ' OR ({m}='A' AND ({c} LIKE '0%' OR {c} LIKE '3%')))"
        )
    elif s == "STAR":
        clause = f"({c} LIKE '688%' OR {c} LIKE '689%')"
    elif s == "CHINEXT":
        clause = f"({c} LIKE '300%' OR {c} LIKE '301%')"
    elif s == "MAIN_SH":
        clause = (
            f"({c} LIKE '60%' AND {c} NOT LIKE '688%' AND {c} NOT LIKE '689%')"
        )
    elif s == "MAIN_SZ":
        clause = f"({c} LIKE '000%' OR {c} LIKE '001%')"
    elif s == "SME":
        clause = f"({c} LIKE '002%')"
    elif s == "BJ":
        clause = f"({m}='BJ' OR {c} LIKE '92%')"
    else:
        clause = (
            f"({m} IN ('A','SH','SZ') OR "
            f"({c} LIKE '0%' OR {c} LIKE '3%' OR {c} LIKE '6%' OR {c} LIKE '92%'))"
        )

    return f" AND {clause}", []
