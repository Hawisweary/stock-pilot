import sqlite3

from services.market_filter import MARKET_SCOPES, normalize_scope, scope_sql


def _count(scope: str) -> int:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE stocks (id INTEGER, code TEXT, market TEXT, is_active INTEGER)"""
    )
    rows = [
        (1, "600519", "A", 1),
        (2, "688256", "SH", 1),
        (3, "300750", "SZ", 1),
        (4, "000001", "SZ", 1),
        (5, "002415", "SZ", 1),
        (6, "920001", "SH", 1),
        (7, "AAPL", "US", 1),
    ]
    conn.executemany("INSERT INTO stocks VALUES (?,?,?,?)", rows)
    clause, params = scope_sql(scope, market_col="market", code_col="code")
    sql = f"SELECT COUNT(*) FROM stocks WHERE is_active=1{clause}"
    return conn.execute(sql, params).fetchone()[0]


def test_normalize_scope_legacy_market():
    assert normalize_scope(market="A") == "A"
    assert normalize_scope(scope="SH") == "SH"
    assert normalize_scope(scope="ALL") == "ALL"


def test_scope_a_includes_legacy_and_exchanges():
    assert _count("A") == 6


def test_scope_sh():
    assert _count("SH") == 3  # 600519(A), 688256, 920001


def test_scope_sz():
    assert _count("SZ") == 3


def test_scope_star():
    assert _count("STAR") == 1


def test_scope_chinext():
    assert _count("CHINEXT") == 1


def test_scope_labels():
    assert MARKET_SCOPES["SH"] == "沪市"
