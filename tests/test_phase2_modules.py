"""Phase II 灰度发布与数据补全单元测试"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import config
from services.gray_release import _bucket, gray_status, in_gray_bucket
from services.financial_backfill import backfill_interest_coverage


class TestGrayRelease(unittest.TestCase):
    def test_bucket_deterministic(self):
        self.assertEqual(_bucket("abc"), _bucket("abc"))
        self.assertNotEqual(_bucket("a"), _bucket("b"))

    def test_gray_status_shape(self):
        s = gray_status("test-client")
        self.assertIn("bucket", s)
        self.assertIn("in_gray", s)
        self.assertIn("gray_release_pct", s)

    def test_in_gray_respects_flags(self):
        orig_dual = config.DUAL_SCORE_UI
        orig_pct = config.GRAY_RELEASE_PCT
        try:
            config.DUAL_SCORE_UI = False
            config.GRAY_RELEASE_PCT = 100
            self.assertFalse(in_gray_bucket("any"))
            config.DUAL_SCORE_UI = True
            config.GRAY_RELEASE_PCT = 0
            self.assertFalse(in_gray_bucket("any"))
        finally:
            config.DUAL_SCORE_UI = orig_dual
            config.GRAY_RELEASE_PCT = orig_pct


class TestInterestBackfill(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        conn = sqlite3.connect(self.db_path)
        conn.executescript("""
            CREATE TABLE stocks (id INTEGER PRIMARY KEY, code TEXT, is_active INTEGER DEFAULT 1);
            CREATE TABLE financial_indicators (id INTEGER PRIMARY KEY, stock_id INTEGER, calc_date TEXT);
            CREATE TABLE financial_reports (
                stock_id INTEGER, report_type TEXT, period_end_date TEXT,
                operating_profit REAL, financing_cf REAL, total_liabilities REAL
            );
        """)
        conn.execute("INSERT INTO stocks VALUES (1,'600000',1)")
        conn.execute("INSERT INTO financial_indicators VALUES (1,1,'2026-01-01')")
        conn.execute(
            """INSERT INTO financial_reports VALUES
               (1,'annual','2025-12-31',1000.0,-200.0,5000.0)"""
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        os.unlink(self.db_path)

    def test_backfill_sets_ic_ratio(self):
        r = backfill_interest_coverage(self.db_path)
        self.assertEqual(r["updated"], 1)
        conn = sqlite3.connect(self.db_path)
        ic = conn.execute(
            "SELECT interest_coverage_ratio FROM financial_indicators WHERE stock_id=1"
        ).fetchone()[0]
        conn.close()
        self.assertIsNotNone(ic)
        self.assertGreater(ic, 0)


if __name__ == "__main__":
    unittest.main()
