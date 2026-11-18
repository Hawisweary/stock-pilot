"""P6 / R1 静态索引的解析逻辑(不依赖数据库、不联网)。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from baseline_common import (  # noqa: E402
    _norm_fe,
    extract_sql_tables,
    match_score,
    norm_backend_path,
    path_match,
)


def test_extract_write_and_read_tables():
    w, r = extract_sql_tables("INSERT OR REPLACE INTO stock_daily_quotes (a) SELECT a FROM tmp_q JOIN stocks s ON 1")
    assert w == {"stock_daily_quotes"}
    assert r == {"tmp_q", "stocks"}


def test_delete_from_counts_as_write_not_read():
    w, r = extract_sql_tables("DELETE FROM factor_values WHERE date < ?")
    assert w == {"factor_values"} and r == set()


def test_update_and_to_sql():
    w, _ = extract_sql_tables("UPDATE portfolios SET cash=? WHERE id=?")
    assert w == {"portfolios"}
    w, _ = extract_sql_tables("df.to_sql('valuation_scores', conn, if_exists='append')")
    assert w == {"valuation_scores"}


def test_non_sql_text_ignored():
    assert extract_sql_tables("从 stocks 读取数据") == (set(), set())


def test_frontend_path_normalization():
    assert _norm_fe("/api/stocks/${id}/quotes?days=30") == "/api/stocks/{}/quotes"
    assert _norm_fe("/api/macro/indicators${q}") == "/api/macro/indicators"
    assert _norm_fe("/api/portfolio/portfolios/${id}/sync-turtle${suffix}") == "/api/portfolio/portfolios/{}/sync-turtle"


def test_backend_path_match_and_score():
    be = norm_backend_path("/api/stocks/{stock_id}/kline")
    assert be == "/api/stocks/{}/kline"
    assert path_match(be, "/api/stocks/{}/kline")
    assert not path_match(be, "/api/stocks/{}")
    # 前端 /api/stocks/{}/kline 更像 {stock_id}/kline,而不是 valuation/{stock_id}
    assert match_score(be, "/api/stocks/{}/kline") > match_score("/api/stocks/valuation/{}", "/api/stocks/{}/kline")
    assert match_score("/api/us/list", "/api/us/{}") < match_score("/api/us/{}", "/api/us/{}")
