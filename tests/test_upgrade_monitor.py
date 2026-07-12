"""升级监控与试点测试"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import config
from services.upgrade_monitor import (
    get_data_quality_metrics,
    get_migration_progress,
    get_upgrade_dashboard,
)
from services.score_compare import run_compare


class UpgradeMonitorTest(unittest.TestCase):
    def _make_db(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        path = tmp.name
        tmp.close()
        conn = sqlite3.connect(path)
        conn.executescript("""
            CREATE TABLE stocks (id INTEGER PRIMARY KEY, code TEXT, name TEXT, is_active INTEGER, industry_sw TEXT);
            CREATE TABLE financial_indicators (stock_id INTEGER, calc_date TEXT, interest_coverage_ratio REAL);
            CREATE TABLE comprehensive_scores (
                stock_id INTEGER, calc_date TEXT, composite_score REAL, fundamental_score REAL
            );
            CREATE TABLE factor_scores (stock_id INTEGER, calc_date TEXT, composite_score REAL);
        """)
        conn.execute("INSERT INTO stocks VALUES (1,'600000','浦发',1,'银行')")
        conn.execute("INSERT INTO stocks VALUES (2,'600036','招商',1,'')")
        conn.execute("INSERT INTO financial_indicators VALUES (1,'2026-01-01',3.5)")
        conn.execute("INSERT INTO comprehensive_scores VALUES (1,'2026-01-01',70,70)")
        conn.commit()
        conn.close()
        return path

    def test_industry_coverage_alert(self):
        path = self._make_db()
        dq = get_data_quality_metrics(path)
        self.assertEqual(dq["active_stocks"], 2)
        self.assertEqual(dq["industry_coverage_pct"], 50.0)
        self.assertFalse(dq["industry_coverage_ok"])
        self.assertTrue(any(a["metric"] == "industry_coverage_pct" for a in dq["alerts"]))
        os.unlink(path)

    def test_migration_progress(self):
        path = self._make_db()
        mig = get_migration_progress(path)
        self.assertIn("factor_history_progress_pct", mig)
        self.assertFalse(mig["gates"]["factor_merge_ready"])
        os.unlink(path)

    def test_upgrade_dashboard(self):
        path = self._make_db()
        dash = get_upgrade_dashboard(path)
        self.assertIn("data_quality", dash)
        self.assertIn("migration", dash)
        self.assertFalse(dash["all_ok"])
        os.unlink(path)

    def test_score_compare(self):
        path = self._make_db()
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO factor_scores VALUES (1,'2026-01-01',60)")
        conn.commit()
        conn.close()
        r = run_compare(path)
        self.assertEqual(r["compared_count"], 1)
        self.assertEqual(r["mean_diff"], 10.0)
        os.unlink(path)


if __name__ == "__main__":
    unittest.main()
