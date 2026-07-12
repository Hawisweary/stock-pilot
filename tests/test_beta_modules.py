"""Beta 模块单元测试 — IC 引擎、回测、组合"""
from __future__ import annotations

import math
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import config
from services import ic_engine, backtest_engine
from services.beta_health import get_beta_health


class TestIcEngine(unittest.TestCase):
    def test_pearson_perfect(self):
        self.assertAlmostEqual(ic_engine.pearson([1, 2, 3, 4, 5], [2, 4, 6, 8, 10]), 1.0)

    def test_rank_ic(self):
        r = ic_engine.rank_ic([1, 2, 3, 4, 5], [5, 4, 3, 2, 1])
        self.assertIsNotNone(r)
        self.assertLess(r, 0)


class TestBetaHealth(unittest.TestCase):
    def test_health_keys(self):
        h = get_beta_health()
        self.assertIn("backtest_ready", h)
        self.assertIn("issues", h)


class TestBacktestEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        cls.db_path = cls.tmp.name
        cls.tmp.close()
        config.DB_PATH = cls.db_path
        os.environ["AFR_PORTFOLIO_RELAX_SESSION"] = "1"
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
        for i in range(30):
            dt = (base - timedelta(days=30 - i)).strftime("%Y-%m-%d")
            for sid, px in [(1, 10 + i * 0.1), (2, 20 + i * 0.05)]:
                conn.execute(
                    "INSERT INTO stock_daily_quotes VALUES (?,?,?,?)",
                    (sid, dt, px, 1000000),
                )
            conn.execute(
                "INSERT INTO comprehensive_scores VALUES (?,?,?,?,?,?,?,?,?,?)",
                (1, dt, 60 + i * 0.2, 55, 50, 50, 50, 50, 50, 50),
            )
            conn.execute(
                "INSERT INTO comprehensive_scores VALUES (?,?,?,?,?,?,?,?,?,?)",
                (2, dt, 40 + i * 0.1, 45, 50, 50, 50, 50, 50, 50),
            )
        conn.commit()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        try:
            os.unlink(cls.db_path)
        except OSError:
            pass

    def test_run_backtest_composite(self):
        r = backtest_engine.run_backtest(days=20, top_n=1, strategy="composite", rebalance="weekly")
        self.assertNotIn("error", r)
        self.assertIn("total_return_pct", r)
        self.assertIn("benchmark", r)

    def test_parameter_scan(self):
        r = backtest_engine.run_parameter_scan(days=20, top_n_list=[1, 2], min_score_list=[40, 50])
        self.assertTrue(r.get("results"))


class TestPortfolio(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        cls.db_path = cls.tmp.name
        cls.tmp.close()
        config.DB_PATH = cls.db_path
        os.environ["AFR_PORTFOLIO_RELAX_SESSION"] = "1"
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
        today = date.today().strftime("%Y-%m-%d")
        conn.execute("INSERT INTO stocks VALUES (1,'600000','浦发银行',1),(2,'600036','招商银行',1)")
        for sid, px in [(1, 10.0), (2, 20.0)]:
            conn.execute("INSERT INTO stock_daily_quotes VALUES (?,?,?,?)", (sid, today, px, 1e6))
        conn.execute(
            "INSERT INTO comprehensive_scores VALUES (?,?,?,?,?,?,?,?,?,?)",
            (1, today, 80, 70, 60, 50, 50, 50, 50, 50),
        )
        conn.execute(
            "INSERT INTO comprehensive_scores VALUES (?,?,?,?,?,?,?,?,?,?)",
            (2, today, 60, 50, 50, 50, 50, 50, 50, 50),
        )
        conn.commit()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        try:
            os.unlink(cls.db_path)
        except OSError:
            pass

    def setUp(self):
        import importlib
        import services.portfolio_svc as ps

        importlib.reload(ps)
        self.svc = ps
        conn = sqlite3.connect(self.db_path)
        ps._ensure_tables(conn)
        for t in ("portfolio_lots", "trade_journal", "portfolio_positions", "portfolio_snapshots", "portfolios"):
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
        conn.close()

    def test_buy_and_sell(self):
        pf = self.svc.create_portfolio("test", 50000)
        r = self.svc.trade(pf["id"], "600000", "buy", 1000)
        self.assertNotIn("error", r)
        self.assertEqual(r["shares"], 1000)
        self.assertLess(r["cash_delta"], 0)
        self.assertIn("price_source", r)
        self.assertIn("quote_date", r)
        detail = self.svc.get_portfolio(pf["id"])
        self.assertEqual(len(detail["positions"]), 1)
        self.assertIn("pricing", detail)

    def test_reject_invalid_shares(self):
        pf = self.svc.create_portfolio("test", 50000)
        r = self.svc.trade(pf["id"], "600000", "buy", 50)
        self.assertIn("error", r)

    def test_t1_blocks_same_day_sell(self):
        pf = self.svc.create_portfolio("test", 50000)
        self.svc.trade(pf["id"], "600000", "buy", 1000)
        r = self.svc.trade(pf["id"], "600000", "sell", 1000)
        self.assertIn("error", r)
        self.assertIn("T+1", r["error"])

    def test_t1_allows_yesterday_lot(self):
        pf = self.svc.create_portfolio("test", 50000)
        conn = sqlite3.connect(self.db_path)
        yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        conn.execute(
            """INSERT INTO portfolio_lots (portfolio_id, stock_id, shares, avg_cost, buy_date)
               VALUES (?,?,?,?,?)""",
            (pf["id"], 1, 1000, 10.0, yesterday),
        )
        conn.execute(
            "INSERT INTO portfolio_positions (portfolio_id, stock_id, shares, avg_cost, buy_date) VALUES (?,?,?,?,?)",
            (pf["id"], 1, 1000, 10.0, yesterday),
        )
        conn.commit()
        conn.close()
        r = self.svc.trade(pf["id"], "600000", "sell", 1000)
        self.assertNotIn("error", r)

    def test_addon_buy_t1_locks_new_shares(self):
        pf = self.svc.create_portfolio("test", 100000)
        yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO portfolio_lots (portfolio_id, stock_id, shares, avg_cost, buy_date) VALUES (?,?,?,?,?)",
            (pf["id"], 1, 1000, 10.0, yesterday),
        )
        conn.execute(
            "INSERT INTO portfolio_positions (portfolio_id, stock_id, shares, avg_cost, buy_date) VALUES (?,?,?,?,?)",
            (pf["id"], 1, 1000, 10.0, yesterday),
        )
        conn.commit()
        conn.close()
        self.svc.trade(pf["id"], "600000", "buy", 500)
        detail = self.svc.get_portfolio(pf["id"])
        pos = detail["positions"][0]
        self.assertEqual(pos["shares"], 1500)
        self.assertEqual(pos["sellable_shares"], 1000)
        self.assertEqual(pos["t1_locked"], 500)

    def test_build_top_n(self):
        pf = self.svc.create_portfolio("test", 100000)
        r = self.svc.build_from_top_n(pf["id"], top_n=1, min_score=50)
        self.assertNotIn("error", r)
        self.assertEqual(r["count"], 1)
        detail = self.svc.get_portfolio(pf["id"])
        self.assertEqual(len(detail["positions"]), 1)

    def test_trading_rules(self):
        from services.trading_rules import split_cost, validate_shares, resolve_strategy

        self.assertIsNotNone(validate_shares(50))
        self.assertIsNone(validate_shares(100))
        cash, comm, tax = split_cost(10000, "sell")
        self.assertGreater(comm, 0)
        self.assertGreater(tax, 0)
        self.assertLess(cash, 10000)
        self.assertEqual(resolve_strategy("valuation"), ("val", "val_score"))
        self.assertIsNone(resolve_strategy("invalid_xyz"))

    def test_invalid_strategy_rejected(self):
        pf = self.svc.create_portfolio("test", 50000)
        r = self.svc.build_from_top_n(pf["id"], strategy="not_a_strategy")
        self.assertIn("error", r)

    def test_weighted_build(self):
        pf = self.svc.create_portfolio("test", 200000)
        conn = sqlite3.connect(self.db_path)
        today = date.today().strftime("%Y-%m-%d")
        conn.execute("INSERT OR REPLACE INTO stock_daily_quotes VALUES (2,?,20.0,1e6)", (today,))
        conn.execute(
            "UPDATE comprehensive_scores SET composite_score=40, calc_date=? WHERE stock_id=2",
            (today,),
        )
        conn.commit()
        conn.close()
        r = self.svc.build_from_top_n(pf["id"], top_n=2, min_score=30, pos_style="weighted")
        self.assertNotIn("error", r)
        self.assertEqual(r["count"], 2)
        self.assertEqual(r["pos_style"], "weighted")


class TestPortfolioAnalytics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        cls.db_path = cls.tmp.name
        cls.tmp.close()
        config.DB_PATH = cls.db_path
        os.environ["AFR_PORTFOLIO_RELAX_SESSION"] = "1"
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
        today = date.today().strftime("%Y-%m-%d")
        conn.execute("INSERT INTO stocks VALUES (1,'600000','浦发',1)")
        conn.execute("INSERT INTO stock_daily_quotes VALUES (?,?,?,?)", (1, today, 10.0, 1e6))
        conn.execute(
            "INSERT INTO comprehensive_scores VALUES (?,?,?,?,?,?,?,?,?,?)",
            (1, today, 80, 70, 60, 50, 50, 50, 50, 50),
        )
        conn.commit()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        try:
            os.unlink(cls.db_path)
        except OSError:
            pass

    def setUp(self):
        import importlib
        import services.portfolio_analytics as pa
        import services.portfolio_svc as ps

        importlib.reload(ps)
        importlib.reload(pa)
        self.svc = ps
        conn = sqlite3.connect(self.db_path)
        ps._ensure_tables(conn)
        for t in ("portfolio_lots", "trade_journal", "portfolio_positions", "portfolio_snapshots", "portfolios"):
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
        conn.close()
        self.pf = ps.create_portfolio("an", 100000)

    def test_metrics_and_export(self):
        self.svc.trade(self.pf["id"], "600000", "buy", 1000)
        from services import portfolio_analytics

        m = portfolio_analytics.compute_metrics(self.pf["id"])
        self.assertIn("total_return_pct", m)
        csv = portfolio_analytics.export_portfolio_csv(self.pf["id"])
        self.assertIn("600000", csv)

    def test_preview_build(self):
        from services import portfolio_analytics

        r = portfolio_analytics.preview_build_top(self.pf["id"], top_n=1)
        self.assertEqual(len(r["preview"]), 1)
        self.assertEqual(r["preview"][0]["code"], "600000")

    def test_estimate_fees(self):
        from services import portfolio_analytics

        r = portfolio_analytics.estimate_trade_fees("600000", "buy", 100)
        self.assertLess(r["cash_delta"], 0)

    def test_journal_stats(self):
        from services import portfolio_analytics

        stats = portfolio_analytics._journal_stats([
            {"code": "600000", "action": "BUY", "shares": 1000, "price": 10, "trade_date": "2026-01-01"},
            {"code": "600000", "action": "SELL", "shares": 1000, "price": 11, "trade_date": "2026-01-05"},
        ])
        self.assertEqual(stats["closed_trades"], 1)
        self.assertGreater(stats["realized_pnl"], 0)


class TestTradePricing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        cls.db_path = cls.tmp.name
        cls.tmp.close()
        config.DB_PATH = cls.db_path
        os.environ["AFR_PORTFOLIO_RELAX_SESSION"] = "1"
        conn = sqlite3.connect(cls.db_path)
        conn.executescript("""
            CREATE TABLE stocks (id INTEGER PRIMARY KEY, code TEXT, name TEXT, is_active INTEGER DEFAULT 1);
            CREATE TABLE stock_daily_quotes (stock_id INTEGER, trade_date TEXT, close REAL, volume REAL);
        """)
        today = date.today().strftime("%Y-%m-%d")
        conn.execute("INSERT INTO stocks VALUES (1,'600000','浦发',1)")
        conn.execute("INSERT INTO stock_daily_quotes VALUES (?,?,?,?)", (1, today, 10.0, 1e6))
        conn.commit()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        try:
            os.unlink(cls.db_path)
        except OSError:
            pass

    def test_eod_price_with_slippage(self):
        import importlib
        import services.trade_pricing as tp

        importlib.reload(tp)
        conn = sqlite3.connect(self.db_path)
        q = tp.resolve_trade_price("600000", 1, "buy", conn)
        conn.close()
        self.assertIsNone(q.error)
        self.assertEqual(q.source, "eod_close")
        self.assertGreater(q.price, q.raw_price)

    def test_apply_slippage_sell(self):
        from services.trade_pricing import apply_slippage

        self.assertLess(apply_slippage(10.0, "sell"), 10.0)

    def test_limit_up_blocks_buy(self):
        import importlib
        import services.trade_pricing as tp

        importlib.reload(tp)
        conn = sqlite3.connect(self.db_path)
        ctx = tp.get_market_context(conn)
        q = tp.TradeQuote(
            price=11.0, raw_price=10.0, quote_date="2026-01-01", source="realtime",
            market_mode="intraday", slippage_pct=0.001, trade_date="2026-01-01",
            limit_up=11.0, limit_down=9.0, label="test",
        )
        err = tp.check_limit("buy", q.price, q.limit_up or 0, q.limit_down or 0)
        self.assertIsNotNone(err)
        conn.close()

    def test_pricing_context(self):
        from services.trade_pricing import pricing_context_dict

        ctx = pricing_context_dict()
        self.assertIn("mode", ctx)
        self.assertIn("slippage_pct", ctx)


if __name__ == "__main__":
    unittest.main()
