"""阶段 III — IC 稳定性 + 因子合成预设"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import config
from services.ic_stability import review_factor_ic, MIN_IC_PERIODS, MIN_ABS_IR
from services.factor_merge_preset import run_preset_merges, TECH_MERGE_INPUTS


class TestIcStability(unittest.TestCase):
    def test_review_shape(self):
        r = review_factor_ic(["F009"], max_dates=60)
        self.assertIn("ic_stable_ready", r)
        self.assertIn("factors", r)
        self.assertEqual(len(r["factors"]), 1)

    def test_stable_threshold_constants(self):
        self.assertGreaterEqual(MIN_IC_PERIODS, 20)
        self.assertGreater(MIN_ABS_IR, 0)


class TestFactorMergePreset(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        cls.db_path = cls.tmp.name
        cls.tmp.close()
        config.DB_PATH = cls.db_path
        config.FACTOR_MERGE_ENABLED = True
        conn = sqlite3.connect(cls.db_path)
        conn.executescript("""
            CREATE TABLE stocks (id INTEGER PRIMARY KEY, code TEXT, is_active INTEGER DEFAULT 1);
            CREATE TABLE factor_registry (factor_id TEXT PRIMARY KEY, name TEXT, category TEXT, formula TEXT);
            CREATE TABLE factor_values (stock_id INTEGER, date TEXT, factor_id TEXT, value REAL,
                PRIMARY KEY (stock_id, date, factor_id));
            CREATE TABLE stock_daily_quotes (stock_id INTEGER, trade_date TEXT, close REAL, volume REAL);
        """)
        for i in range(1, 35):
            conn.execute("INSERT INTO stocks VALUES (?, ?, 1)", (i, f"60000{i:03d}"))
        for fid in TECH_MERGE_INPUTS:
            conn.execute(
                "INSERT INTO factor_registry VALUES (?,?,?,?)",
                (fid, fid, "tech", "test"),
            )
        base = date.today()
        for d in range(65):
            dt = (base - timedelta(days=65 - d)).strftime("%Y-%m-%d")
            for sid in range(1, 35):
                for fid in TECH_MERGE_INPUTS:
                    conn.execute(
                        "INSERT INTO factor_values VALUES (?,?,?,?)",
                        (sid, dt, fid, float(sid + d * 0.01)),
                    )
        conn.commit()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.db_path)

    def test_preset_merge_runs(self):
        import services.factor_merge as fm

        fm.FACTOR_MERGE_ENABLED = True
        r = run_preset_merges(skip_ic_check=True)
        self.assertNotIn("error", r)
        self.assertEqual(r["success_count"], 3)
        self.assertEqual(len(r["results"]), 3)


if __name__ == "__main__":
    unittest.main()
