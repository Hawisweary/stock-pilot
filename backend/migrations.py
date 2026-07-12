"""
数据库 schema 版本迁移
"""
from __future__ import annotations

import sqlite3

CURRENT_SCHEMA_VERSION = 34

MIGRATIONS: list[tuple[int, str]] = [
    (2, """
        ALTER TABLE factor_scores ADD COLUMN momentum_score REAL;
    """),
    (3, """
        ALTER TABLE stocks ADD COLUMN industry_sw TEXT DEFAULT '';
        CREATE TABLE IF NOT EXISTS factor_weights (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            weight_quality REAL NOT NULL DEFAULT 0.30,
            weight_growth REAL NOT NULL DEFAULT 0.25,
            weight_value REAL NOT NULL DEFAULT 0.20,
            weight_momentum REAL NOT NULL DEFAULT 0.10,
            weight_risk REAL NOT NULL DEFAULT 0.15
        );
        INSERT OR IGNORE INTO factor_weights (id) VALUES (1);
    """),
    (4, """
        CREATE TABLE IF NOT EXISTS valuation_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL REFERENCES stocks(id),
            as_of_date TEXT NOT NULL,
            pe_ttm REAL,
            pb REAL,
            market_cap REAL,
            dividend_yield REAL,
            source TEXT DEFAULT 'tencent',
            UNIQUE(stock_id, as_of_date)
        );
        CREATE INDEX IF NOT EXISTS idx_valuation_stock ON valuation_snapshots(stock_id, as_of_date DESC);
    """),
    (5, """
        CREATE TABLE IF NOT EXISTS trend_alerts_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL REFERENCES stocks(id),
            data_hash TEXT NOT NULL,
            alerts_json TEXT NOT NULL DEFAULT '[]',
            source TEXT DEFAULT 'rules',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(stock_id, data_hash)
        );
        CREATE TABLE IF NOT EXISTS industry_aliases (
            raw_name TEXT PRIMARY KEY,
            industry_sw TEXT NOT NULL
        );
        ALTER TABLE ai_analyses ADD COLUMN input_hash TEXT DEFAULT '';
    """),
    (6, """
        CREATE TABLE IF NOT EXISTS fetch_jobs (
            stock_id INTEGER PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'pending',
            running INTEGER NOT NULL DEFAULT 0,
            quotes INTEGER DEFAULT 0,
            financials INTEGER DEFAULT 0,
            indicators INTEGER DEFAULT 0,
            errors_json TEXT DEFAULT '[]',
            error TEXT DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS report_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL REFERENCES stocks(id),
            title TEXT NOT NULL DEFAULT '',
            file_name TEXT NOT NULL DEFAULT '',
            file_path TEXT NOT NULL,
            content_hash TEXT NOT NULL DEFAULT '',
            extracted_text TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_report_docs_stock ON report_documents(stock_id);
        CREATE TABLE IF NOT EXISTS report_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL REFERENCES report_documents(id),
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            UNIQUE(document_id, chunk_index)
        );
    """),
    (7, """
        CREATE VIRTUAL TABLE IF NOT EXISTS report_chunks_fts USING fts5(
            chunk_id UNINDEXED,
            document_id UNINDEXED,
            stock_id UNINDEXED,
            content,
            tokenize='unicode61'
        );
    """),
    (8, """
        CREATE TABLE IF NOT EXISTS comprehensive_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            calc_date TEXT NOT NULL,
            fundamental_score REAL,
            technical_score REAL,
            sentiment_score REAL,
            composite_score REAL,
            breakdown_json TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            capital_score REAL,
            policy_score REAL,
            mood_score REAL,
            val_score REAL,
            debate_locked INTEGER DEFAULT 0,
            risk_score REAL,
            max_drawdown_60d REAL,
            volatility_20d REAL,
            UNIQUE(stock_id, calc_date)
        );
        CREATE INDEX IF NOT EXISTS idx_comp_scores_stock_date
            ON comprehensive_scores(stock_id, calc_date DESC);
    """),
    (9, """
        CREATE TABLE IF NOT EXISTS portfolios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '',
            initial_cash REAL NOT NULL DEFAULT 100000,
            cash REAL NOT NULL DEFAULT 100000,
            created_at TEXT NOT NULL DEFAULT (date('now'))
        );
        CREATE TABLE IF NOT EXISTS portfolio_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            portfolio_id INTEGER NOT NULL,
            stock_id INTEGER NOT NULL,
            shares INTEGER NOT NULL DEFAULT 0,
            avg_cost REAL NOT NULL DEFAULT 0,
            buy_date TEXT,
            UNIQUE(portfolio_id, stock_id)
        );
        CREATE TABLE IF NOT EXISTS portfolio_lots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            portfolio_id INTEGER NOT NULL,
            stock_id INTEGER NOT NULL,
            shares INTEGER NOT NULL,
            avg_cost REAL NOT NULL,
            buy_date TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_portfolio_lots_pf ON portfolio_lots(portfolio_id, stock_id);
        CREATE TABLE IF NOT EXISTS trade_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            portfolio_id INTEGER NOT NULL,
            stock_id INTEGER,
            action TEXT NOT NULL,
            shares INTEGER NOT NULL DEFAULT 0,
            price REAL NOT NULL DEFAULT 0,
            trade_date TEXT NOT NULL,
            code TEXT,
            name TEXT,
            commission REAL DEFAULT 0,
            tax REAL DEFAULT 0,
            cash_delta REAL DEFAULT 0,
            reason TEXT DEFAULT '',
            strategy TEXT DEFAULT '',
            notes TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_trade_journal_pf ON trade_journal(portfolio_id, trade_date DESC);
        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            portfolio_id INTEGER NOT NULL,
            snapshot_date TEXT NOT NULL,
            total_value REAL NOT NULL,
            PRIMARY KEY (portfolio_id, snapshot_date)
        );
    """),
    (10, """
        ALTER TABLE portfolios ADD COLUMN owner_id TEXT DEFAULT 'default';
        ALTER TABLE portfolios ADD COLUMN rebalance_schedule TEXT DEFAULT 'none';
        ALTER TABLE portfolios ADD COLUMN last_rebalance_date TEXT;
        ALTER TABLE portfolios ADD COLUMN max_weight_pct REAL DEFAULT 30;
        ALTER TABLE portfolios ADD COLUMN min_cash_pct REAL DEFAULT 5;
        ALTER TABLE portfolios ADD COLUMN default_strategy TEXT DEFAULT 'composite';
        ALTER TABLE portfolios ADD COLUMN default_top_n INTEGER DEFAULT 5;
        ALTER TABLE portfolios ADD COLUMN default_min_score REAL DEFAULT 50;
        ALTER TABLE portfolios ADD COLUMN default_pos_style TEXT DEFAULT 'equal';
    """),
    (11, """
        CREATE TABLE IF NOT EXISTS ml_predictions (
            stock_id INTEGER NOT NULL,
            pred_date TEXT NOT NULL,
            score REAL NOT NULL,
            model_version TEXT NOT NULL DEFAULT 'v0',
            PRIMARY KEY (stock_id, pred_date, model_version)
        );
        CREATE TABLE IF NOT EXISTS job_runs (
            id TEXT PRIMARY KEY,
            job_type TEXT NOT NULL,
            payload_json TEXT,
            status TEXT NOT NULL,
            result_json TEXT,
            error TEXT,
            created_at TEXT,
            started_at TEXT,
            finished_at TEXT
        );
    """),
    (12, """
        ALTER TABLE financial_indicators ADD COLUMN interest_coverage_ratio REAL;
        ALTER TABLE valuation_snapshots ADD COLUMN peg_ratio REAL;
    """),
    (13, """
        ALTER TABLE valuation_snapshots ADD COLUMN ps_ratio REAL;
    """),
    (14, """
        CREATE TABLE IF NOT EXISTS debate_v2 (
            stock_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            original_score REAL,
            adjusted_score REAL,
            debate_json TEXT,
            PRIMARY KEY (stock_id, date)
        );
        CREATE INDEX IF NOT EXISTS idx_debate_v2_stock ON debate_v2(stock_id, date DESC);
    """),
    (15, """
        CREATE TABLE IF NOT EXISTS score_gap_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            target_date TEXT NOT NULL,
            alert_key TEXT,
            mode TEXT,
            job_id TEXT,
            active_stocks_count INTEGER,
            stock_scope_json TEXT,
            sync_rate_all_before REAL,
            sync_rate_required_before REAL,
            sync_rate_all_after REAL,
            sync_rate_required_after REAL,
            gap_summary_json TEXT,
            actions_json TEXT,
            alert_detail_json TEXT,
            filled_count INTEGER DEFAULT 0,
            skipped_count INTEGER DEFAULT 0,
            error_count INTEGER DEFAULT 0,
            duration_ms INTEGER,
            triggered_by TEXT DEFAULT 'api',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_gap_log_date ON score_gap_log(target_date, created_at);
        CREATE INDEX IF NOT EXISTS idx_gap_log_alert ON score_gap_log(alert_key, event_type, created_at);
    """),
    (16, """
        ALTER TABLE debate_v2 ADD COLUMN input_hash TEXT;
        CREATE INDEX IF NOT EXISTS idx_debate_v2_input_hash ON debate_v2(stock_id, date, input_hash);
        CREATE TABLE IF NOT EXISTS debate_batch_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            job_id TEXT,
            mode TEXT,
            calc_date TEXT,
            target_date TEXT,
            total INTEGER,
            to_run INTEGER,
            llm_count INTEGER,
            light_count INTEGER,
            skipped INTEGER,
            completed INTEGER,
            error_count INTEGER,
            batch_retry_passes INTEGER DEFAULT 0,
            concurrency INTEGER,
            tier_counts_json TEXT,
            skip_reasons_json TEXT,
            duration_ms INTEGER,
            triggered_by TEXT DEFAULT 'api',
            detail_json TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_debate_batch_log_job ON debate_batch_log(job_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_debate_batch_log_date ON debate_batch_log(target_date, created_at);
    """),
    (17, """
        CREATE TABLE IF NOT EXISTS stock_lifecycle (
            stock_id INTEGER PRIMARY KEY,
            code TEXT NOT NULL,
            list_date TEXT,
            delist_date TEXT,
            source TEXT DEFAULT 'stocks',
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_lifecycle_list ON stock_lifecycle(list_date);
        CREATE INDEX IF NOT EXISTS idx_lifecycle_delist ON stock_lifecycle(delist_date);
        CREATE TABLE IF NOT EXISTS financial_calendar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            period_end_date TEXT NOT NULL,
            report_type TEXT DEFAULT 'annual',
            disclosure_date TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'conservative+45',
            UNIQUE(stock_id, period_end_date, report_type)
        );
        CREATE INDEX IF NOT EXISTS idx_fin_cal_stock ON financial_calendar(stock_id, period_end_date);
        CREATE INDEX IF NOT EXISTS idx_fin_cal_disc ON financial_calendar(stock_id, disclosure_date);
        CREATE TABLE IF NOT EXISTS factor_values_wide (
            stock_id INTEGER NOT NULL,
            calc_date TEXT NOT NULL,
            f001 REAL, f002 REAL, f003 REAL, f004 REAL, f005 REAL,
            f006 REAL, f007 REAL, f008 REAL, f009 REAL, f010 REAL,
            f011 REAL, f012 REAL, f013 REAL, f014 REAL, f015 REAL,
            quality_flags TEXT DEFAULT '{}',
            updated_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (stock_id, calc_date)
        );
        CREATE INDEX IF NOT EXISTS idx_fvw_date ON factor_values_wide(calc_date);
        CREATE INDEX IF NOT EXISTS idx_fvw_stock ON factor_values_wide(stock_id, calc_date DESC);
    """),
    (18, """
        CREATE TABLE IF NOT EXISTS factor_combinations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            factor_ids_json TEXT NOT NULL,
            weight_method TEXT NOT NULL DEFAULT 'equal',
            weights_json TEXT,
            output_factor_id TEXT,
            formula_json TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_factor_combos_out ON factor_combinations(output_factor_id);
    """),
    (19, """
        CREATE TABLE IF NOT EXISTS factor_compute_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mode TEXT NOT NULL,
            target_date TEXT,
            stocks_touched INTEGER DEFAULT 0,
            cells_written INTEGER DEFAULT 0,
            duration_ms INTEGER,
            detail_json TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_factor_compute_log_created ON factor_compute_log(created_at DESC);
    """),
    (20, """
        CREATE TABLE IF NOT EXISTS factor_expressions (
            factor_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            formula TEXT NOT NULL,
            kind TEXT DEFAULT 'timeseries',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS factor_gp_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT,
            status TEXT DEFAULT 'pending',
            population INTEGER,
            generations INTEGER,
            candidates_json TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS factor_metrics_cache (
            cache_key TEXT PRIMARY KEY,
            calc_date TEXT NOT NULL,
            benchmark_mode TEXT NOT NULL,
            stock_count INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now'))
        );
    """),
    (21, """
        -- data_fetch_log.source 标记抓取数据源
    """),
    (22, """
        CREATE TABLE IF NOT EXISTS stock_ex_rights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL REFERENCES stocks(id),
            ex_date TEXT NOT NULL,
            cash_div REAL DEFAULT 0,
            bonus_ratio REAL DEFAULT 0,
            transfer_ratio REAL DEFAULT 0,
            plan_notice_date TEXT DEFAULT '',
            source TEXT DEFAULT 'eastmoney',
            UNIQUE(stock_id, ex_date)
        );
        CREATE INDEX IF NOT EXISTS idx_ex_rights_stock ON stock_ex_rights(stock_id, ex_date);
        CREATE TABLE IF NOT EXISTS stock_announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL REFERENCES stocks(id),
            title TEXT NOT NULL,
            ann_type TEXT DEFAULT '',
            pub_date TEXT NOT NULL,
            url TEXT DEFAULT '',
            pdf_url TEXT DEFAULT '',
            source TEXT DEFAULT 'eastmoney',
            art_code TEXT DEFAULT '',
            UNIQUE(stock_id, art_code)
        );
        CREATE INDEX IF NOT EXISTS idx_ann_stock_date ON stock_announcements(stock_id, pub_date DESC);
    """),
    (23, """
        ALTER TABLE stocks ADD COLUMN industry_sw2 TEXT DEFAULT '';
        ALTER TABLE financial_reports ADD COLUMN accounts_receivable REAL;
        CREATE TABLE IF NOT EXISTS stock_fund_flow_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL REFERENCES stocks(id),
            trade_date TEXT NOT NULL,
            main_net_inflow REAL,
            super_large_inflow REAL,
            main_net_5d REAL,
            source TEXT DEFAULT 'eastmoney',
            UNIQUE(stock_id, trade_date)
        );
        CREATE INDEX IF NOT EXISTS idx_fund_flow_stock ON stock_fund_flow_daily(stock_id, trade_date DESC);
        CREATE TABLE IF NOT EXISTS sector_fund_flow_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sector_code TEXT NOT NULL,
            sector_name TEXT NOT NULL DEFAULT '',
            trade_date TEXT NOT NULL,
            net_inflow REAL,
            net_inflow_pct REAL,
            change_pct REAL,
            rs_csi300_20d REAL,
            source TEXT DEFAULT 'eastmoney',
            UNIQUE(sector_code, trade_date)
        );
        CREATE INDEX IF NOT EXISTS idx_sector_flow_date ON sector_fund_flow_daily(trade_date DESC);
        CREATE TABLE IF NOT EXISTS stock_v5_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL REFERENCES stocks(id),
            calc_date TEXT NOT NULL,
            revenue_yoy_q REAL,
            profit_yoy_q REAL,
            growth_qoq_delta REAL,
            cfo_np REAL,
            accrual_ratio REAL,
            cfo_yoy REAL,
            debt_ratio REAL,
            debt_vs_industry REAL,
            quality_tier INTEGER,
            growth_tier INTEGER,
            source TEXT DEFAULT 'computed',
            UNIQUE(stock_id, calc_date)
        );
        CREATE INDEX IF NOT EXISTS idx_v5_metrics_stock ON stock_v5_metrics(stock_id, calc_date DESC);
    """),
    (24, """
        CREATE TABLE IF NOT EXISTS stock_eps_forecast (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL REFERENCES stocks(id),
            as_of_date TEXT NOT NULL,
            eps_fy1 REAL,
            eps_fy2 REAL,
            eps_fy1_year INTEGER,
            eps_fy2_year INTEGER,
            analyst_count INTEGER,
            rating_buy INTEGER,
            industry_board TEXT DEFAULT '',
            revision_3m_pct REAL,
            source TEXT DEFAULT 'eastmoney',
            UNIQUE(stock_id, as_of_date)
        );
        CREATE INDEX IF NOT EXISTS idx_eps_forecast_stock ON stock_eps_forecast(stock_id, as_of_date DESC);
        CREATE TABLE IF NOT EXISTS industry_eps_revision_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            industry_sw2 TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            revision_3m_pct REAL,
            stock_count INTEGER DEFAULT 0,
            tier INTEGER,
            source TEXT DEFAULT 'computed',
            UNIQUE(industry_sw2, trade_date)
        );
        CREATE INDEX IF NOT EXISTS idx_ind_eps_rev_date ON industry_eps_revision_daily(trade_date DESC);
        CREATE TABLE IF NOT EXISTS risk_flags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL REFERENCES stocks(id),
            flag_date TEXT NOT NULL,
            flag_type TEXT NOT NULL,
            severity TEXT DEFAULT 'high',
            detail TEXT DEFAULT '',
            source TEXT DEFAULT 'computed',
            UNIQUE(stock_id, flag_date, flag_type)
        );
        CREATE INDEX IF NOT EXISTS idx_risk_flags_stock ON risk_flags(stock_id, flag_date DESC);
        ALTER TABLE stock_announcements ADD COLUMN event_type TEXT DEFAULT '';
    """),
    (25, """
        CREATE TABLE IF NOT EXISTS policy_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pub_date TEXT NOT NULL,
            title TEXT NOT NULL,
            level INTEGER NOT NULL DEFAULT 0,
            industries_json TEXT DEFAULT '[]',
            source TEXT DEFAULT 'announcement',
            UNIQUE(pub_date, title)
        );
        CREATE INDEX IF NOT EXISTS idx_policy_events_date ON policy_events(pub_date DESC);
        CREATE TABLE IF NOT EXISTS policy_industry_response (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL REFERENCES policy_events(id),
            industry_sw2 TEXT NOT NULL,
            excess_return_20d REAL,
            coef REAL,
            UNIQUE(event_id, industry_sw2)
        );
        CREATE INDEX IF NOT EXISTS idx_policy_resp_event ON policy_industry_response(event_id);
        CREATE TABLE IF NOT EXISTS stock_mood_v5_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL REFERENCES stocks(id),
            calc_date TEXT NOT NULL,
            mood_raw REAL,
            mood_tier INTEGER,
            turnover_pct REAL,
            news_heat REAL,
            main_net_5d REAL,
            capital_tier INTEGER,
            flipped INTEGER DEFAULT 0,
            source TEXT DEFAULT 'proxy_v5',
            UNIQUE(stock_id, calc_date)
        );
        CREATE INDEX IF NOT EXISTS idx_mood_v5_stock ON stock_mood_v5_daily(stock_id, calc_date DESC);
    """),
    (26, """
        ALTER TABLE comprehensive_scores ADD COLUMN quality_score REAL;
        ALTER TABLE comprehensive_scores ADD COLUMN industry_score REAL;
        ALTER TABLE comprehensive_scores ADD COLUMN market_env_score REAL;
        ALTER TABLE comprehensive_scores ADD COLUMN composite_v5 REAL;
        ALTER TABLE comprehensive_scores ADD COLUMN veto_status TEXT DEFAULT 'ok';
        ALTER TABLE comprehensive_scores ADD COLUMN v5_breakdown_json TEXT DEFAULT '{}';
    """),
    (27, """
        CREATE TABLE IF NOT EXISTS event_title_cache (
            title_key TEXT PRIMARY KEY,
            event_type TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'llm',
            hit_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_event_title_cache_updated
            ON event_title_cache(updated_at DESC);
    """),
    (28, """
        CREATE TABLE IF NOT EXISTS lhb_daily (
            stock_id INTEGER NOT NULL,
            trade_date TEXT NOT NULL,
            code TEXT NOT NULL,
            net_buy REAL,
            buy_amount REAL,
            sell_amount REAL,
            deal_amount REAL,
            change_pct REAL,
            turnover_pct REAL,
            reason TEXT DEFAULT '',
            source TEXT DEFAULT 'eastmoney',
            PRIMARY KEY (stock_id, trade_date)
        );
        CREATE INDEX IF NOT EXISTS idx_lhb_daily_date ON lhb_daily(trade_date DESC);
    """),
    (29, """
        CREATE TABLE IF NOT EXISTS lhb_market_daily (
            trade_date TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            close REAL,
            net_buy REAL,
            buy_amount REAL,
            sell_amount REAL,
            deal_amount REAL,
            change_pct REAL,
            turnover_pct REAL,
            reason TEXT DEFAULT '',
            source TEXT DEFAULT 'eastmoney',
            synced_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (trade_date, code)
        );
        CREATE INDEX IF NOT EXISTS idx_lhb_market_daily_date ON lhb_market_daily(trade_date DESC);
    """),
    (30, """
        CREATE TABLE IF NOT EXISTS fetch_step_status (
            stock_id INTEGER NOT NULL,
            step TEXT NOT NULL,
            status TEXT NOT NULL,
            message TEXT DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (stock_id, step)
        );
        CREATE INDEX IF NOT EXISTS idx_fetch_step_status_stock ON fetch_step_status(stock_id);
    """),
    (31, """
        ALTER TABLE portfolios ADD COLUMN default_combination_id INTEGER;
        ALTER TABLE portfolios ADD COLUMN default_lookback INTEGER DEFAULT 20;
    """),
    (32, """
        ALTER TABLE portfolios ADD COLUMN default_sector_window INTEGER DEFAULT 5;
        ALTER TABLE portfolios ADD COLUMN default_per_sector INTEGER DEFAULT 2;
    """),
    (33, """
        ALTER TABLE portfolio_positions ADD COLUMN turtle_stop_price REAL;
    """),
    (34, """
        DROP VIEW IF EXISTS v_stock_scores;
        CREATE VIEW v_stock_scores AS
        SELECT
            s.id          AS stock_id,
            s.code,
            s.name,
            s.market,
            s.sector,
            s.industry,
            s.industry_sw,
            s.is_active,
            cs.calc_date,
            cs.composite_v5                AS score,
            cs.veto_status,
            cs.v5_breakdown_json           AS breakdown_json,
            cs.fundamental_score,
            cs.technical_score,
            cs.sentiment_score,
            cs.capital_score,
            cs.policy_score,
            cs.mood_score,
            cs.val_score,
            cs.quality_score,
            cs.industry_score,
            cs.market_env_score
        FROM stocks s
        LEFT JOIN (
            SELECT cs2.*
            FROM comprehensive_scores cs2
            INNER JOIN (
                SELECT stock_id, MAX(calc_date) AS md
                FROM comprehensive_scores
                GROUP BY stock_id
            ) t ON cs2.stock_id = t.stock_id AND cs2.calc_date = t.md
        ) cs ON s.id = cs.stock_id
        WHERE s.is_active = 1;
    """),
    (35, """
        CREATE TABLE IF NOT EXISTS score_change_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id    INTEGER NOT NULL,
            calc_date   TEXT    NOT NULL,
            old_score   REAL,
            new_score   REAL    NOT NULL,
            delta       REAL    NOT NULL,
            old_veto    TEXT,
            new_veto    TEXT,
            veto_changed INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            UNIQUE(stock_id, calc_date),
            FOREIGN KEY(stock_id) REFERENCES stocks(id)
        );
        CREATE INDEX IF NOT EXISTS idx_scl_stock ON score_change_log(stock_id, calc_date DESC);
        CREATE INDEX IF NOT EXISTS idx_scl_delta ON score_change_log(ABS(delta) DESC);
        CREATE INDEX IF NOT EXISTS idx_scl_created ON score_change_log(created_at DESC);
    """),
    (36, """
        CREATE TABLE IF NOT EXISTS stock_score_profiles (
            stock_id       INTEGER NOT NULL,
            calc_date      TEXT    NOT NULL,
            profile        TEXT    NOT NULL CHECK(profile IN ('momentum','dividend')),
            score          REAL    NOT NULL,
            breakdown_json TEXT,
            PRIMARY KEY (stock_id, calc_date, profile),
            FOREIGN KEY(stock_id) REFERENCES stocks(id)
        );
        CREATE INDEX IF NOT EXISTS idx_ssp_stock ON stock_score_profiles(stock_id, profile, calc_date DESC);
        CREATE INDEX IF NOT EXISTS idx_ssp_profile ON stock_score_profiles(profile, calc_date DESC);
    """),
    (37, """
        -- 影子表：每只股票只存最新行的 id
        CREATE TABLE IF NOT EXISTS comprehensive_scores_latest (
            stock_id INTEGER PRIMARY KEY,
            cs_id    INTEGER NOT NULL
        );

        -- 初始化：填入历史数据中每只股票 calc_date 最大的那行
        INSERT OR REPLACE INTO comprehensive_scores_latest (stock_id, cs_id)
        SELECT cs.stock_id, cs.id
        FROM comprehensive_scores cs
        INNER JOIN (
            SELECT stock_id, MAX(calc_date) AS md
            FROM comprehensive_scores
            GROUP BY stock_id
        ) t ON cs.stock_id = t.stock_id AND cs.calc_date = t.md;

        -- INSERT 触发器：新行 calc_date >= 当前最新时才替换
        CREATE TRIGGER IF NOT EXISTS trg_cs_latest_insert
        AFTER INSERT ON comprehensive_scores
        BEGIN
            INSERT INTO comprehensive_scores_latest (stock_id, cs_id)
            VALUES (NEW.stock_id, NEW.id)
            ON CONFLICT(stock_id) DO UPDATE SET cs_id = NEW.id
            WHERE NEW.calc_date >= (
                SELECT calc_date FROM comprehensive_scores WHERE id = excluded.cs_id
            );
        END;

        -- UPDATE 触发器：calc_date 变化时重新比较
        CREATE TRIGGER IF NOT EXISTS trg_cs_latest_update
        AFTER UPDATE OF calc_date ON comprehensive_scores
        BEGIN
            INSERT INTO comprehensive_scores_latest (stock_id, cs_id)
            VALUES (NEW.stock_id, NEW.id)
            ON CONFLICT(stock_id) DO UPDATE SET cs_id = NEW.id
            WHERE NEW.calc_date >= (
                SELECT calc_date FROM comprehensive_scores WHERE id = excluded.cs_id
            );
        END;

        -- 重建视图：走影子表点查，不再 GROUP BY 全表
        DROP VIEW IF EXISTS v_stock_scores;
        CREATE VIEW v_stock_scores AS
        SELECT
            s.id          AS stock_id,
            s.code,
            s.name,
            s.market,
            s.sector,
            s.industry,
            s.industry_sw,
            s.is_active,
            cs.calc_date,
            cs.composite_v5                AS score,
            cs.veto_status,
            cs.v5_breakdown_json           AS breakdown_json,
            cs.fundamental_score,
            cs.technical_score,
            cs.sentiment_score,
            cs.capital_score,
            cs.policy_score,
            cs.mood_score,
            cs.val_score,
            cs.quality_score,
            cs.industry_score,
            cs.market_env_score
        FROM stocks s
        LEFT JOIN comprehensive_scores_latest l ON s.id = l.stock_id
        LEFT JOIN comprehensive_scores cs ON cs.id = l.cs_id
        WHERE s.is_active = 1;
    """),
    (38, """
        -- get_market_context() 里 SELECT MAX(trade_date) FROM stock_daily_quotes
        -- 在 180万行表上找不到可用索引(现有 idx_quotes_date 是 (stock_id, trade_date)
        -- 复合索引，对不带 stock_id 条件的 MAX 全局聚合用不上)，退化成全表扫描，
        -- 单次 ~0.3s。组合列表/盈亏摘要等按组合数循环调用时被放大成秒级延迟。
        CREATE INDEX IF NOT EXISTS idx_quotes_trade_date_only ON stock_daily_quotes(trade_date);
    """),
    (39, """
        ALTER TABLE stocks ADD COLUMN industry_sw3 TEXT DEFAULT '';
    """),
    (40, """
        ALTER TABLE stock_concept_boards ADD COLUMN source TEXT NOT NULL DEFAULT 'unknown';
    """),
    (41, """
        CREATE TABLE IF NOT EXISTS trade_calendar (
            cal_date TEXT PRIMARY KEY,
            is_open  INTEGER NOT NULL
        );
    """),
    (42, """
        CREATE TABLE IF NOT EXISTS stock_company_info (
            stock_id      INTEGER PRIMARY KEY,
            com_name      TEXT DEFAULT '',
            chairman      TEXT DEFAULT '',
            manager       TEXT DEFAULT '',
            secretary     TEXT DEFAULT '',
            reg_capital   REAL,
            setup_date    TEXT DEFAULT '',
            province      TEXT DEFAULT '',
            city          TEXT DEFAULT '',
            website       TEXT DEFAULT '',
            employees     INTEGER,
            main_business TEXT DEFAULT '',
            business_scope TEXT DEFAULT '',
            introduction  TEXT DEFAULT '',
            updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS stock_managers (
            stock_id   INTEGER NOT NULL,
            name       TEXT NOT NULL,
            lev        TEXT DEFAULT '',
            title      TEXT DEFAULT '',
            gender     TEXT DEFAULT '',
            edu        TEXT DEFAULT '',
            birthday   TEXT DEFAULT '',
            begin_date TEXT DEFAULT '',
            end_date   TEXT DEFAULT '',
            PRIMARY KEY (stock_id, name, title, begin_date)
        );
        CREATE INDEX IF NOT EXISTS idx_stock_managers_stock ON stock_managers(stock_id);
    """),
    (43, """
        ALTER TABLE valuation_snapshots ADD COLUMN pe REAL;
        ALTER TABLE valuation_snapshots ADD COLUMN turnover_rate REAL;
        ALTER TABLE valuation_snapshots ADD COLUMN turnover_rate_f REAL;
        ALTER TABLE valuation_snapshots ADD COLUMN volume_ratio REAL;
        ALTER TABLE valuation_snapshots ADD COLUMN dividend_yield_ttm REAL;
        ALTER TABLE valuation_snapshots ADD COLUMN total_share REAL;
        ALTER TABLE valuation_snapshots ADD COLUMN float_share REAL;
        ALTER TABLE valuation_snapshots ADD COLUMN free_share REAL;
        ALTER TABLE valuation_snapshots ADD COLUMN limit_status INTEGER;
    """),
    (44, """
        ALTER TABLE financial_reports ADD COLUMN rd_exp REAL;
        ALTER TABLE financial_reports ADD COLUMN money_cap REAL;
        ALTER TABLE financial_reports ADD COLUMN inventories REAL;
        ALTER TABLE financial_reports ADD COLUMN goodwill REAL;
        ALTER TABLE financial_reports ADD COLUMN fix_assets REAL;
        CREATE TABLE IF NOT EXISTS hsgt_top10_daily (
            stock_id    INTEGER NOT NULL,
            trade_date  TEXT NOT NULL,
            market_type TEXT NOT NULL,
            name        TEXT DEFAULT '',
            close       REAL,
            change      REAL,
            rank        INTEGER,
            amount      REAL,
            net_amount  REAL,
            buy         REAL,
            sell        REAL,
            PRIMARY KEY (stock_id, trade_date, market_type)
        );
        CREATE INDEX IF NOT EXISTS idx_hsgt_top10_date ON hsgt_top10_daily(trade_date);
    """),
    (45, """
        CREATE TABLE IF NOT EXISTS earnings_forecast (
            stock_id        INTEGER NOT NULL,
            period_end_date TEXT NOT NULL,
            ann_date        TEXT DEFAULT '',
            type            TEXT DEFAULT '',
            p_change_min    REAL,
            p_change_max    REAL,
            net_profit_min  REAL,
            net_profit_max  REAL,
            last_parent_net REAL,
            summary         TEXT DEFAULT '',
            change_reason   TEXT DEFAULT '',
            PRIMARY KEY (stock_id, period_end_date)
        );
        CREATE TABLE IF NOT EXISTS earnings_express (
            stock_id        INTEGER NOT NULL,
            period_end_date TEXT NOT NULL,
            ann_date        TEXT DEFAULT '',
            revenue         REAL,
            operate_profit  REAL,
            n_income        REAL,
            total_assets    REAL,
            diluted_eps     REAL,
            diluted_roe     REAL,
            yoy_sales       REAL,
            yoy_dedu_np     REAL,
            perf_summary    TEXT DEFAULT '',
            PRIMARY KEY (stock_id, period_end_date)
        );
    """),
    (46, """
        CREATE TABLE IF NOT EXISTS stock_moneyflow_l2_daily (
            stock_id       INTEGER NOT NULL,
            trade_date     TEXT NOT NULL,
            buy_sm_amount  REAL,
            sell_sm_amount REAL,
            buy_md_amount  REAL,
            sell_md_amount REAL,
            buy_lg_amount  REAL,
            sell_lg_amount REAL,
            buy_elg_amount REAL,
            sell_elg_amount REAL,
            net_mf_amount  REAL,
            PRIMARY KEY (stock_id, trade_date)
        );
        CREATE TABLE IF NOT EXISTS stock_moneyflow_dc_daily (
            stock_id         INTEGER NOT NULL,
            trade_date       TEXT NOT NULL,
            net_amount       REAL,
            net_amount_rate  REAL,
            buy_elg_amount   REAL,
            buy_lg_amount    REAL,
            buy_md_amount    REAL,
            buy_sm_amount    REAL,
            PRIMARY KEY (stock_id, trade_date)
        );
    """),
    (47, """
        CREATE TABLE IF NOT EXISTS market_new_high_low_daily (
            trade_date TEXT PRIMARY KEY,
            close      REAL,
            high20     INTEGER,
            low20      INTEGER,
            high60     INTEGER,
            low60      INTEGER,
            high120    INTEGER,
            low120     INTEGER
        );
        CREATE TABLE IF NOT EXISTS stock_lhb_period_stats (
            stock_id       INTEGER NOT NULL,
            period         TEXT NOT NULL,
            updated_date   TEXT NOT NULL,
            last_lhb_date  TEXT DEFAULT '',
            close          REAL,
            change_pct     REAL,
            lhb_count      INTEGER,
            lhb_net_amount REAL,
            lhb_buy_amount REAL,
            lhb_sell_amount REAL,
            inst_buy_count  INTEGER,
            inst_sell_count INTEGER,
            inst_net_amount REAL,
            chg_1m REAL,
            chg_3m REAL,
            chg_6m REAL,
            chg_1y REAL,
            PRIMARY KEY (stock_id, period)
        );
        CREATE TABLE IF NOT EXISTS market_summary_daily (
            trade_date TEXT NOT NULL,
            exchange   TEXT NOT NULL,
            category   TEXT NOT NULL,
            count      INTEGER,
            turnover   REAL,
            total_mv   REAL,
            circ_mv    REAL,
            pe_avg     REAL,
            PRIMARY KEY (trade_date, exchange, category)
        );
    """),
    (48, """
        CREATE TABLE IF NOT EXISTS earnings_surprise_factor (
            stock_id        INTEGER NOT NULL,
            period_end_date TEXT NOT NULL,
            actual_source   TEXT NOT NULL,
            actual_growth   REAL,
            guided_growth   REAL,
            guided_ann_date TEXT DEFAULT '',
            actual_ann_date TEXT DEFAULT '',
            surprise_pct    REAL,
            tier            INTEGER,
            PRIMARY KEY (stock_id, period_end_date)
        );
        CREATE TABLE IF NOT EXISTS capital_resonance_daily (
            stock_id         INTEGER NOT NULL,
            trade_date       TEXT NOT NULL,
            l2_net_amount    REAL,
            lhb_net_buy      REAL,
            hsgt_net_amount  REAL,
            resonance_count  INTEGER,
            tier             INTEGER,
            PRIMARY KEY (stock_id, trade_date)
        );
        CREATE INDEX IF NOT EXISTS idx_capital_resonance_date ON capital_resonance_daily(trade_date);
    """),
]


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _safe_add_column(conn: sqlite3.Connection, table: str, column: str, ddl: str):
    if not _table_exists(conn, table):
        return
    if column not in _table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def run_migrations(conn: sqlite3.Connection) -> int:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            version INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    row = conn.execute("SELECT version FROM schema_version WHERE id=1").fetchone()
    current = row[0] if row else 1
    if not row:
        conn.execute("INSERT INTO schema_version (id, version) VALUES (1, 1)")
        conn.commit()

    for version, sql_block in MIGRATIONS:
        if version <= current:
            continue
        print(f"[DB] 迁移 v{current} -> v{version}")
        # v2 momentum_score — 可能已存在
        if version == 2:
            _safe_add_column(conn, "factor_scores", "momentum_score", "momentum_score REAL")
        elif version == 3:
            _safe_add_column(conn, "stocks", "industry_sw", "industry_sw TEXT DEFAULT ''")
            for stmt in sql_block.strip().split(";"):
                s = stmt.strip()
                if s and "ADD COLUMN" not in s:
                    conn.execute(s)
        elif version == 5:
            for stmt in sql_block.strip().split(";"):
                s = stmt.strip()
                if not s:
                    continue
                if "ai_analyses ADD COLUMN" in s or "ALTER TABLE ai_analyses" in s:
                    _safe_add_column(conn, "ai_analyses", "input_hash", "input_hash TEXT DEFAULT ''")
                    continue
                try:
                    conn.execute(s)
                except sqlite3.OperationalError as e:
                    if "duplicate column" not in str(e).lower():
                        raise
        elif version == 9:
            for stmt in sql_block.strip().split(";"):
                s = stmt.strip()
                if s:
                    try:
                        conn.execute(s)
                    except sqlite3.OperationalError as e:
                        if "duplicate column" not in str(e).lower():
                            raise
            for col, ddl in [
                ("stock_id", "stock_id INTEGER"),
                ("code", "code TEXT"),
                ("name", "name TEXT"),
                ("commission", "commission REAL DEFAULT 0"),
                ("tax", "tax REAL DEFAULT 0"),
                ("cash_delta", "cash_delta REAL DEFAULT 0"),
                ("reason", "reason TEXT DEFAULT ''"),
                ("strategy", "strategy TEXT DEFAULT ''"),
                ("notes", "notes TEXT DEFAULT ''"),
            ]:
                _safe_add_column(conn, "trade_journal", col, ddl)
            _safe_add_column(conn, "portfolio_positions", "buy_date", "buy_date TEXT")
        elif version == 10:
            for col, ddl in [
                ("owner_id", "owner_id TEXT DEFAULT 'default'"),
                ("rebalance_schedule", "rebalance_schedule TEXT DEFAULT 'none'"),
                ("last_rebalance_date", "last_rebalance_date TEXT"),
                ("max_weight_pct", "max_weight_pct REAL DEFAULT 30"),
                ("min_cash_pct", "min_cash_pct REAL DEFAULT 5"),
                ("default_strategy", "default_strategy TEXT DEFAULT 'composite'"),
                ("default_top_n", "default_top_n INTEGER DEFAULT 5"),
                ("default_min_score", "default_min_score REAL DEFAULT 50"),
                ("default_pos_style", "default_pos_style TEXT DEFAULT 'equal'"),
            ]:
                _safe_add_column(conn, "portfolios", col, ddl)
        elif version == 11:
            for stmt in sql_block.strip().split(";"):
                s = stmt.strip()
                if s:
                    try:
                        conn.execute(s)
                    except sqlite3.OperationalError as e:
                        if "already exists" not in str(e).lower():
                            raise
        elif version == 17:
            for stmt in sql_block.strip().split(";"):
                s = stmt.strip()
                if s:
                    try:
                        conn.execute(s)
                    except sqlite3.OperationalError as e:
                        if "already exists" not in str(e).lower():
                            raise
            _safe_add_column(conn, "stock_daily_quotes", "adj_close", "adj_close REAL")
            _safe_add_column(
                conn, "stock_daily_quotes", "is_suspended", "is_suspended INTEGER DEFAULT 0"
            )
        elif version == 21:
            _safe_add_column(conn, "data_fetch_log", "source", "source TEXT DEFAULT ''")
        elif version == 22:
            for stmt in sql_block.strip().split(";"):
                s = stmt.strip()
                if s:
                    try:
                        conn.execute(s)
                    except sqlite3.OperationalError as e:
                        if "already exists" not in str(e).lower():
                            raise
        elif version == 23:
            for col, ddl in [
                ("industry_sw2", "industry_sw2 TEXT DEFAULT ''"),
                ("accounts_receivable", "accounts_receivable REAL"),
            ]:
                table = "stocks" if col == "industry_sw2" else "financial_reports"
                _safe_add_column(conn, table, col, ddl)
            for stmt in sql_block.strip().split(";"):
                s = stmt.strip()
                if not s or s.upper().startswith("ALTER TABLE"):
                    continue
                try:
                    conn.execute(s)
                except sqlite3.OperationalError as e:
                    if "already exists" not in str(e).lower():
                        raise
            for col, ddl in [
                ("social_financing", "social_financing REAL"),
                ("social_financing_yoy", "social_financing_yoy REAL"),
                ("social_financing_mom", "social_financing_mom REAL"),
                ("bond_yield_10y", "bond_yield_10y REAL"),
                ("usd_cnh", "usd_cnh REAL"),
            ]:
                _safe_add_column(conn, "macro_indicators", col, ddl)
        elif version == 24:
            for stmt in sql_block.strip().split(";"):
                s = stmt.strip()
                if not s or s.upper().startswith("ALTER TABLE"):
                    continue
                try:
                    conn.execute(s)
                except sqlite3.OperationalError as e:
                    if "already exists" not in str(e).lower():
                        raise
            _safe_add_column(conn, "stock_announcements", "event_type", "event_type TEXT DEFAULT ''")
            if _table_exists(conn, "stock_news"):
                _safe_add_column(conn, "stock_news", "event_type", "event_type TEXT DEFAULT ''")
        elif version == 25:
            for stmt in sql_block.strip().split(";"):
                s = stmt.strip()
                if not s:
                    continue
                try:
                    conn.execute(s)
                except sqlite3.OperationalError as e:
                    if "already exists" not in str(e).lower():
                        raise
        elif version == 26:
            for col, ddl in [
                ("quality_score", "quality_score REAL"),
                ("industry_score", "industry_score REAL"),
                ("market_env_score", "market_env_score REAL"),
                ("composite_v5", "composite_v5 REAL"),
                ("veto_status", "veto_status TEXT DEFAULT 'ok'"),
                ("v5_breakdown_json", "v5_breakdown_json TEXT DEFAULT '{}'"),
            ]:
                _safe_add_column(conn, "comprehensive_scores", col, ddl)
        else:
            for stmt in sql_block.strip().split(";"):
                s = stmt.strip()
                if s:
                    try:
                        conn.execute(s)
                    except sqlite3.OperationalError as e:
                        if "duplicate column" not in str(e).lower():
                            raise
        conn.execute(
            "UPDATE schema_version SET version=?, updated_at=datetime('now') WHERE id=1",
            (version,),
        )
        conn.commit()
        current = version

    _seed_industry_aliases(conn)
    return current


def _seed_industry_aliases(conn: sqlite3.Connection):
    """常见行业别名 -> 申万一级"""
    from services.industry_normalize import INDUSTRY_ALIASES

    for raw, sw in INDUSTRY_ALIASES.items():
        conn.execute(
            "INSERT OR IGNORE INTO industry_aliases (raw_name, industry_sw) VALUES (?, ?)",
            (raw, sw),
        )
    conn.commit()
