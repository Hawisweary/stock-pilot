import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from migrations import run_migrations, CURRENT_SCHEMA_VERSION


def test_migrations_apply():
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        conn = sqlite3.connect(f.name)
        conn.execute(
            """CREATE TABLE factor_scores (
                stock_id INTEGER, calc_date TEXT,
                profitability_score REAL, growth_score REAL,
                safety_score REAL, value_score REAL, composite_score REAL,
                score_detail_json TEXT,
                UNIQUE(stock_id, calc_date)
            )"""
        )
        conn.execute(
            """CREATE TABLE stocks (
                id INTEGER PRIMARY KEY, code TEXT, industry TEXT
            )"""
        )
        conn.commit()
        version = run_migrations(conn)
        assert version == CURRENT_SCHEMA_VERSION
        cols = {r[1] for r in conn.execute("PRAGMA table_info(factor_scores)")}
        assert "momentum_score" in cols
        conn.close()
