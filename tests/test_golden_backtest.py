"""Golden 回测回归 + 量化基建测试"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import config
from services import backtest_engine, ic_engine
from services.backtest_rust_adapter import run_backtest_with_engine
from services.job_queue import enqueue, get_job, JobStatus
from services.timeseries_store import SQLiteStore


GOLDEN = [
    {"days": 20, "top_n": 1, "strategy": "composite", "rebalance": "weekly"},
    {"days": 20, "top_n": 2, "strategy": "val", "rebalance": "monthly"},
    {"days": 15, "top_n": 1, "strategy": "momentum", "rebalance": "weekly", "lookback": 10},
]


class GoldenBacktestFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        cls.db_path = cls.tmp.name
        cls.tmp.close()
        config.DB_PATH = cls.db_path
        conn = sqlite3.connect(cls.db_path)
        conn.executescript("""
            CREATE TABLE stocks (id INTEGER PRIMARY KEY, code TEXT, name TEXT, is_active INTEGER DEFAULT 1);
            CREATE TABLE stock_daily_quotes (stock_id INTEGER, trade_date TEXT, close REAL, volume REAL);
            CREATE TABLE comprehensive_scores (
                stock_id INTEGER, calc_date TEXT, composite_score REAL, fundamental_score REAL,
                technical_score REAL, sentiment_score REAL, capital_score REAL,
                policy_score REAL, mood_score REAL, val_score REAL
            );
        """)
        conn.execute("INSERT INTO stocks VALUES (1,'600000','浦发银行',1),(2,'600036','招商银行',1)")
        base = date.today()
        for i in range(40):
            dt = (base - timedelta(days=40 - i)).strftime("%Y-%m-%d")
            for sid, px in [(1, 10 + i * 0.1), (2, 20 + i * 0.05)]:
                conn.execute("INSERT INTO stock_daily_quotes VALUES (?,?,?,?)", (sid, dt, px, 1000000))
            conn.execute(
                "INSERT INTO comprehensive_scores VALUES (?,?,?,?,?,?,?,?,?,?)",
                (1, dt, 60 + i * 0.2, 55, 50, 50, 50, 50, 50, 50),
            )
            conn.execute(
                "INSERT INTO comprehensive_scores VALUES (?,?,?,?,?,?,?,?,?,?)",
                (2, dt, 40 + i * 0.1, 45, 50, 50, 50, 50, 50, 55),
            )
        conn.commit()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        try:
            os.unlink(cls.db_path)
        except OSError:
            pass

    def test_golden_cases_stable(self):
        baselines = []
        for params in GOLDEN:
            r = backtest_engine.run_backtest(**params)
            self.assertNotIn("error", r, r.get("error"))
            baselines.append(
                {
                    "params": params,
                    "total_return_pct": r["total_return_pct"],
                    "trade_count": r["trade_count"],
                }
            )
        for b in baselines:
            r2 = backtest_engine.run_backtest(**b["params"])
            self.assertAlmostEqual(r2["total_return_pct"], b["total_return_pct"], places=4)
            self.assertEqual(r2["trade_count"], b["trade_count"])

    def test_rust_fallback_to_python(self):
        r = run_backtest_with_engine({"days": 20, "top_n": 1, "strategy": "composite"}, engine="rust")
        self.assertNotIn("error", r)
        self.assertTrue(r.get("rust_fallback") or r.get("params", {}).get("engine") == "python")


class QuantInfraTest(unittest.TestCase):
    def test_sqlite_store_read(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        path = tmp.name
        tmp.close()
        config.DB_PATH = path
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE stocks (id INTEGER PRIMARY KEY, code TEXT, name TEXT, is_active INTEGER)")
        conn.execute(
            "CREATE TABLE stock_daily_quotes (stock_id INTEGER, trade_date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL)"
        )
        conn.execute("INSERT INTO stocks VALUES (1,'600000','浦发银行',1)")
        conn.execute("INSERT INTO stock_daily_quotes VALUES (1,'2026-01-01',10,11,9,10.5,1000)")
        conn.commit()
        conn.close()
        rows = SQLiteStore(path).read_bars(stock_id=1)
        self.assertEqual(len(rows), 1)
        os.unlink(path)

    def test_factor_decay_insufficient(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        path = tmp.name
        tmp.close()
        config.DB_PATH = path
        conn = sqlite3.connect(path)
        conn.executescript("""
            CREATE TABLE stocks (id INTEGER PRIMARY KEY, code TEXT, is_active INTEGER);
            CREATE TABLE factor_values (stock_id INTEGER, date TEXT, factor_id TEXT, value REAL, rank INTEGER,
                PRIMARY KEY (stock_id, date, factor_id));
        """)
        conn.execute("INSERT INTO stocks VALUES (1,'600000',1)")
        conn.execute("INSERT INTO factor_values VALUES (1,'2026-01-01','F001',50,NULL)")
        conn.commit()
        conn.close()
        r = ic_engine.analyze_factor_decay("F001")
        self.assertIn("error", r)
        os.unlink(path)

    def test_job_queue_factor_compute(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        path = tmp.name
        tmp.close()
        config.DB_PATH = path
        conn = sqlite3.connect(path)
        conn.executescript("""
            CREATE TABLE stocks (id INTEGER PRIMARY KEY, code TEXT, is_active INTEGER);
            CREATE TABLE stock_daily_quotes (stock_id INTEGER, trade_date TEXT, close REAL, volume REAL);
            CREATE TABLE comprehensive_scores (
                stock_id INTEGER, calc_date TEXT, composite_score REAL, fundamental_score REAL,
                technical_score REAL, sentiment_score REAL, capital_score REAL,
                policy_score REAL, mood_score REAL, val_score REAL
            );
        """)
        conn.execute("INSERT INTO stocks VALUES (1,'600000',1)")
        conn.execute("INSERT INTO stock_daily_quotes VALUES (1,'2026-01-01',10,1000)")
        conn.execute(
            "INSERT INTO comprehensive_scores VALUES (1,'2026-01-01',60,55,50,50,50,50,50,50)"
        )
        conn.commit()
        conn.close()
        job = enqueue("factor_compute", {"backfill": False})
        import time

        for _ in range(50):
            j = get_job(job.id)
            if j and j.status in (JobStatus.DONE, JobStatus.FAILED):
                break
            time.sleep(0.1)
        j = get_job(job.id)
        self.assertEqual(j.status, JobStatus.DONE)
        os.unlink(path)


if __name__ == "__main__":
    unittest.main()
