"""
AI基本面研究员 - 数据库模块
SQLite 数据库初始化、连接管理、备份
设计参考 FitTracker 项目模式
"""
from __future__ import annotations

import sqlite3
import os
import time
import shutil
import threading
from config import CACHE_DB_PATH, DB_PATH, DATA_DIR

# 全局连接池（单连接模式，WAL 支持并发读）
_conn: sqlite3.Connection | None = None
# 后台抓取线程与 API 共用连接时的写锁（RLock 支持 factor_engine → upsert 嵌套调用）
write_lock = threading.RLock()

# ---- 缓存库（cache.db）----
# 日志/缓存类高频小写入独立成库，与主库批任务的写锁彻底解耦：
# score_gap_log / data_fetch_log / factor_metrics_cache
_CACHE_TABLES_SQL = """
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
CREATE TABLE IF NOT EXISTS data_fetch_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id        INTEGER,
    data_type       TEXT NOT NULL,
    fetch_time      TEXT NOT NULL DEFAULT (datetime('now')),
    status          TEXT NOT NULL DEFAULT 'success',
    records_count   INTEGER DEFAULT 0,
    error_message   TEXT DEFAULT '',
    duration_ms     INTEGER DEFAULT 0,
    source          TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS factor_metrics_cache (
    cache_key TEXT PRIMARY KEY,
    calc_date TEXT NOT NULL,
    benchmark_mode TEXT NOT NULL,
    stock_count INTEGER DEFAULT 0,
    updated_at TEXT DEFAULT (datetime('now'))
);
"""

_cache_tables_ready = False


def cache_connect() -> sqlite3.Connection:
    """获取缓存库连接（每次新建，WAL+短busy_timeout，调用方负责close）。"""
    global _cache_tables_ready
    conn = sqlite3.connect(CACHE_DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    if not _cache_tables_ready:
        conn.executescript(_CACHE_TABLES_SQL)
        conn.commit()
        _cache_tables_ready = True
    return conn


def get_db_path() -> str:
    """获取数据库文件路径"""
    return DB_PATH


def get() -> sqlite3.Connection:
    """获取数据库连接（已初始化）"""
    global _conn
    if _conn is None:
        raise RuntimeError("Database not initialized. Call init() first.")
    return _conn


def is_initialized() -> bool:
    """检查数据库是否已初始化"""
    return _conn is not None and os.path.exists(DB_PATH)


def init(db_path: str | None = None):
    """
    初始化数据库：创建连接、启用WAL模式、创建所有表
    """
    global _conn
    path = db_path or DB_PATH

    # timeout 加大到120s：启动建表/迁移需要熬过后台批任务的长写事务
    _conn = sqlite3.connect(path, check_same_thread=False, timeout=120)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA foreign_keys=ON")
    _conn.execute("PRAGMA busy_timeout=120000")
    _conn.execute("PRAGMA synchronous=NORMAL")  # WAL下NORMAL安全且更快

    _create_tables()
    print(f"[DB] 数据库初始化完成: {path}")
    return _conn


def _create_tables():
    """创建所有数据表"""
    conn = _conn
    if conn is None:
        raise RuntimeError("Connection not initialized")

    # 1. 股票主表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stocks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            code        TEXT NOT NULL UNIQUE,
            name        TEXT NOT NULL,
            market      TEXT NOT NULL DEFAULT 'A',
            sector      TEXT DEFAULT '',
            industry    TEXT DEFAULT '',
            industry_sw TEXT DEFAULT '',
            list_date   TEXT DEFAULT '',
            is_active   INTEGER DEFAULT 1,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stocks_market ON stocks(market)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stocks_code ON stocks(code)")

    # 2. 每日行情
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_daily_quotes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id    INTEGER NOT NULL REFERENCES stocks(id),
            trade_date  TEXT NOT NULL,
            open        REAL,
            high        REAL,
            low         REAL,
            close       REAL,
            volume      REAL,
            amount      REAL,
            change_pct  REAL,
            turnover    REAL,
            UNIQUE(stock_id, trade_date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_quotes_date ON stock_daily_quotes(stock_id, trade_date)")

    # 3. 财务报表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS financial_reports (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id        INTEGER NOT NULL REFERENCES stocks(id),
            report_date     TEXT NOT NULL,
            period_end_date TEXT NOT NULL,
            report_type     TEXT NOT NULL DEFAULT 'annual',
            revenue             REAL,
            operating_revenue   REAL,
            net_profit          REAL,
            net_profit_parent   REAL,
            gross_profit        REAL,
            operating_profit    REAL,
            total_operating_cost REAL,
            total_assets        REAL,
            total_equity        REAL,
            total_liabilities   REAL,
            current_assets      REAL,
            current_liabilities REAL,
            operating_cf        REAL,
            investing_cf        REAL,
            financing_cf        REAL,
            eps                 REAL,
            bvps                REAL,
            raw_data_json       TEXT DEFAULT '{}',
            UNIQUE(stock_id, period_end_date, report_type)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fin_period ON financial_reports(stock_id, period_end_date)")

    # 4. 财务指标（估值与效率比率）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS financial_indicators (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id        INTEGER NOT NULL REFERENCES stocks(id),
            calc_date       TEXT NOT NULL,
            pe_ttm          REAL,
            pb              REAL,
            ps_ttm          REAL,
            pcf_ttm         REAL,
            roe             REAL,
            roa             REAL,
            gross_margin    REAL,
            net_margin      REAL,
            debt_to_equity  REAL,
            current_ratio   REAL,
            dividend_yield  REAL,
            market_cap      REAL,
            UNIQUE(stock_id, calc_date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ind_calc_date ON financial_indicators(stock_id, calc_date)")

    # 5. 因子评分
    conn.execute("""
        CREATE TABLE IF NOT EXISTS factor_scores (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id            INTEGER NOT NULL REFERENCES stocks(id),
            calc_date           TEXT NOT NULL,
            profitability_score REAL,
            growth_score        REAL,
            safety_score        REAL,
            value_score         REAL,
            momentum_score      REAL,
            composite_score     REAL,
            score_detail_json   TEXT DEFAULT '{}',
            UNIQUE(stock_id, calc_date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scores_date ON factor_scores(calc_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scores_composite ON factor_scores(composite_score DESC)")

    # 6. 数据抓取日志
    conn.execute("""
        CREATE TABLE IF NOT EXISTS data_fetch_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id        INTEGER,
            data_type       TEXT NOT NULL,
            fetch_time      TEXT NOT NULL DEFAULT (datetime('now')),
            status          TEXT NOT NULL DEFAULT 'success',
            records_count   INTEGER DEFAULT 0,
            error_message   TEXT DEFAULT '',
            duration_ms     INTEGER DEFAULT 0,
            source          TEXT DEFAULT ''
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fetch_log_time ON data_fetch_log(fetch_time)")

    # 7. AI 分析记录
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_analyses (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id        INTEGER NOT NULL REFERENCES stocks(id),
            analysis_type   TEXT DEFAULT 'fundamental',
            summary         TEXT,
            strengths_json  TEXT DEFAULT '[]',
            weaknesses_json TEXT DEFAULT '[]',
            factor_commentary_json TEXT DEFAULT '{}',
            valuation_view  TEXT DEFAULT '',
            overall_rating  TEXT DEFAULT '',
            raw_response_json TEXT DEFAULT '{}',
            generated_at    TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    conn.commit()
    from migrations import run_migrations

    try:
        run_migrations(conn)
    except sqlite3.OperationalError as e:
        if "locked" not in str(e).lower():
            raise
        # 后台批任务(如周度同步)长期持有写锁时不阻塞启动：
        # 迁移具备幂等性，跳过后将在下次空闲启动时补跑
        print(f"[DB] 数据库忙,跳过启动迁移(下次启动补跑): {e}")


def backup():
    """创建数据库备份（gzip 压缩，保留最近3个）"""
    import gzip
    import shutil

    if not os.path.exists(DB_PATH):
        return

    backup_dir = os.path.join(DATA_DIR, "backups")
    os.makedirs(backup_dir, exist_ok=True)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(backup_dir, f"afr_backup_{timestamp}.db")

    # 使用 SQLite 在线备份
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(backup_path)
    src.backup(dst)
    src.close()
    dst.close()

    # gzip 压缩后删除原始副本（恢复: gunzip 后直接使用）
    gz_path = backup_path + ".gz"
    with open(backup_path, "rb") as f_in, gzip.open(gz_path, "wb", compresslevel=6) as f_out:
        shutil.copyfileobj(f_in, f_out, length=8 * 1024 * 1024)
    os.remove(backup_path)

    # 滚动清理：压缩备份保留最近3个；历史未压缩 .db 备份只留最新1个
    gz_backups = sorted(
        [f for f in os.listdir(backup_dir) if f.startswith("afr_backup_") and f.endswith(".gz")],
        reverse=True,
    )
    for old in gz_backups[3:]:
        os.remove(os.path.join(backup_dir, old))
    plain = sorted(
        [f for f in os.listdir(backup_dir) if f.startswith("afr_backup_") and f.endswith(".db")],
        reverse=True,
    )
    for old in plain[1:]:
        os.remove(os.path.join(backup_dir, old))

    print(f"[DB] 备份完成: {gz_path}")


def close():
    """关闭数据库连接"""
    global _conn
    if _conn:
        _conn.close()
        _conn = None
        print("[DB] 数据库连接已关闭")
