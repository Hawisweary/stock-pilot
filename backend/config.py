"""全局配置 — 统一数据库路径、API密钥、模型参数"""
import os

# 手动加载 .env（独立脚本直接 import config 时也能拿到密钥，不依赖 app.py 先跑）
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(BASE_DIR), "data")
DB_PATH = os.path.join(DATA_DIR, "afr.db")
DB_READ_PATH = os.getenv("AFR_DB_READ_PATH", os.path.join(DATA_DIR, "afr_read.db"))
# 缓存/日志类表独立库：API 高频小写入与主库批任务写锁彻底解耦
CACHE_DB_PATH = os.getenv("AFR_CACHE_DB_PATH", os.path.join(DATA_DIR, "cache.db"))

# API
# AFR_AUTH_ALL=true 或 AFR_API_KEY_REQUIRED=true 均可启用全路径鉴权（GET 也需 Key）
API_KEY_REQUIRED = (
    os.getenv("AFR_API_KEY_REQUIRED", "false").lower() == "true"
    or os.getenv("AFR_AUTH_ALL", "false").lower() == "true"
)
API_KEY = os.getenv("AFR_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN", "")
# 日线 EOD 批量行情主源(#60):tencent(默认)| tushare。实时现价始终走腾讯,不受此开关影响。
QUOTE_SOURCE = os.getenv("AFR_QUOTE_SOURCE", "tencent").lower()
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-v4-pro")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")

# LLM Client 统一变量（兼容旧命名）
AI_MODEL = os.getenv("AI_MODEL", LLM_MODEL)
AI_PROVIDER = os.getenv("AI_PROVIDER", "deepseek")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", LLM_BASE_URL)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "")

# 调度
SCHEDULE_INTERVAL_MINUTES = int(os.getenv("SCHEDULE_INTERVAL", "30"))
MARKET_CLOSE_HOUR = 15
DATA_FETCH_DAYS = int(os.getenv("DATA_FETCH_DAYS", "2000"))
# 前端 K 线默认展示根数（约 1 年交易日）
KLINE_DISPLAY_DAYS = int(os.getenv("AFR_KLINE_DISPLAY_DAYS", "250"))
# 市场行情页增量同步拉取根数（同一次 HTTP，宜偏大以补滞后）
QUOTE_SYNC_MAX_BARS = int(os.getenv("AFR_QUOTE_SYNC_MAX_BARS", "500"))

# v3.0: SCORE_WEIGHTS 已删除（8维聚合分废弃）。综合分由 composite_v5 唯一提供。
# DEFAULT_SCORE 不再作为 fallback（C2 修复：缺数据维度返回 None，不填 50）。
DEFAULT_SCORE = 50.0  # 保留供历史读取，禁止用于新写入

# V5 折扣系数（A-1/A-2 修复：去掉旧 ×0.6−10 双重惩罚）
V5_QUALITY_DISCOUNT_MULT = float(os.getenv("V5_QUALITY_DISCOUNT_MULT", "0.70"))
# A-4：capital 主力净流出判 -2 的阈值（原 -5e7，放宽至 -1.5亿）
V5_CAPITAL_FLOW_MINUS2_THRESHOLD = float(os.getenv("V5_CAPITAL_FLOW_MINUS2", "-1.5e8"))
# DQ-1：数据质量异常（anomaly_score>=50）对 V5 综合分的折扣
V5_DATA_QUALITY_DISCOUNT_MULT = float(os.getenv("V5_DATA_QUALITY_DISCOUNT_MULT", "0.70"))
# DQ-1：数据质量异常直接排除的阈值（anomaly_score>=80）
V5_DATA_QUALITY_EXCLUDE_THRESHOLD = float(os.getenv("V5_DATA_QUALITY_EXCLUDE_THRESHOLD", "80.0"))

# MR-1：市场状态分类对 V5 维度的动态调整（相对基线权重的增量，会自动归一化）
V5_REGIME_WEIGHT_DELTAS: dict[str, dict[str, float]] = {
    "strong_trend_up": {"technical": 0.05, "capital": 0.03, "valuation": -0.08},
    "weak_trend_up": {"technical": 0.03, "capital": 0.02, "valuation": -0.05},
    "strong_trend_down": {"quality": 0.05, "valuation": 0.05, "technical": -0.05, "capital": -0.05},
    "weak_trend_down": {"quality": 0.03, "valuation": 0.03, "technical": -0.03, "capital": -0.03},
    "high_volatility": {"quality": 0.05, "valuation": 0.03, "technical": -0.05, "capital": -0.03},
    "liquidity_drought": {"quality": 0.05, "valuation": 0.03, "technical": -0.05, "capital": -0.05},
    "oscillation": {},
}

# MR-2：市场状态 → V5 profile 权重（覆盖细粒度 delta，震荡市仍用基线+delta）
V5_REGIME_PROFILE_MAP: dict[str, str] = {
    "strong_trend_up": "momentum",
    "weak_trend_up": "momentum",
    "strong_trend_down": "dividend",
    "weak_trend_down": "dividend",
    "high_volatility": "dividend",
    "liquidity_drought": "dividend",
}

# 市场状态识别基准指数（双轨：CSI300 行业惯例 + CSI800 策略推荐首选）
REGIME_INDEX_CSI300 = os.getenv("AFR_REGIME_INDEX_CSI300", "sh000300")
REGIME_INDEX_CSI800 = os.getenv("AFR_REGIME_INDEX_CSI800", "sh000906")
# 策略推荐系统主基准（L2/L3 切换前仍双轨展示；中期默认 CSI800）
REGIME_PRIMARY_INDEX = os.getenv("AFR_REGIME_PRIMARY_INDEX", REGIME_INDEX_CSI800)

# 市场状态分类阈值（L1 七格；四格 bucket 由 regime_bucket 映射）
REGIME_KLINE_DAYS = int(os.getenv("AFR_REGIME_KLINE_DAYS", "400"))
REGIME_VOL_HIGH = float(os.getenv("AFR_REGIME_VOL_HIGH", "0.17"))
REGIME_AVG_CORR_HIGH = float(os.getenv("AFR_REGIME_AVG_CORR_HIGH", "0.65"))
REGIME_ADX_CHOP = float(os.getenv("AFR_REGIME_ADX_CHOP", "50"))
REGIME_RET20_CHOP_ABS = float(os.getenv("AFR_REGIME_RET20_CHOP_ABS", "0.03"))
REGIME_TREND_DOWN_PVM60 = float(os.getenv("AFR_REGIME_TREND_DOWN_PVM60", "-0.03"))
# L1 趋势判定（P1：强方向性运动优先于高波动）
REGIME_TREND_RET20_UP = float(os.getenv("AFR_REGIME_TREND_RET20_UP", "0.015"))
REGIME_TREND_RET20_DOWN = float(os.getenv("AFR_REGIME_TREND_RET20_DOWN", "-0.015"))
REGIME_TREND_RET20_STRONG = float(os.getenv("AFR_REGIME_TREND_RET20_STRONG", "0.08"))
REGIME_TREND_RET20_STRONG_DOWN = float(os.getenv("AFR_REGIME_TREND_RET20_STRONG_DOWN", "-0.08"))
REGIME_TREND_RET60_STRONG = float(os.getenv("AFR_REGIME_TREND_RET60_STRONG", "0.03"))
REGIME_TREND_RET60_STRONG_DOWN = float(os.getenv("AFR_REGIME_TREND_RET60_STRONG_DOWN", "-0.03"))
REGIME_TREND_RSI_UP = float(os.getenv("AFR_REGIME_TREND_RSI_UP", "52"))
REGIME_TREND_RSI_DOWN = float(os.getenv("AFR_REGIME_TREND_RSI_DOWN", "48"))
# L1 状态持续性：不对称确认期（上涨慢、下跌快）
REGIME_PERSISTENCE_DAYS = int(os.getenv("AFR_REGIME_PERSISTENCE_DAYS", "5"))
REGIME_UP_CONFIRM_DAYS = int(os.getenv("AFR_REGIME_UP_CONFIRM_DAYS", "5"))
REGIME_DOWN_CONFIRM_DAYS = int(os.getenv("AFR_REGIME_DOWN_CONFIRM_DAYS", "2"))
REGIME_VOL_CONFIRM_DAYS = int(os.getenv("AFR_REGIME_VOL_CONFIRM_DAYS", "3"))
REGIME_OSC_CONFIRM_DAYS = int(os.getenv("AFR_REGIME_OSC_CONFIRM_DAYS", "3"))
REGIME_ASYMMETRIC_PERSISTENCE = os.getenv("AFR_REGIME_ASYMMETRIC_PERSISTENCE", "true").lower() in ("1", "true", "yes", "on")

# L2 策略×状态矩阵窗口
REGIME_MATRIX_BACKTEST_DAYS = int(os.getenv("AFR_MATRIX_BACKTEST_DAYS", "500"))
REGIME_MATRIX_LOOKBACK_DAYS = int(os.getenv("AFR_MATRIX_LOOKBACK_DAYS", "730"))
REGIME_MATRIX_MIN_SAMPLES = int(os.getenv("AFR_MATRIX_MIN_SAMPLES", "10"))
REGIME_REC_OUTCOME_HORIZONS = [
    int(x.strip()) for x in os.getenv("AFR_REC_OUTCOME_HORIZONS", "5,20").split(",") if x.strip()
]
MOMENTUM_BEAR_REGIMES = frozenset({"strong_trend_down", "weak_trend_down", "liquidity_drought"})
MOMENTUM_BEAR_MIN_SCORE_BONUS = 15.0
# 动量崩溃：5 日跌幅 + 10 日动量转负
MOMENTUM_CRASH_5D_PCT = -8.0

# 双均线：最小持仓交易日（防震荡鞭梢）
DUAL_MA_MIN_HOLD_DAYS = 10

# 行业轮动拥挤度阈值
SECTOR_CROWDING_WARN = 70
SECTOR_CROWDING_BLOCK = 85

# M1：动量 profile 的 quality_minus2 折扣（比 value 宽松，但仍保留惩罚）
V5_MOMENTUM_QUALITY_DISCOUNT_MULT = float(os.getenv("V5_MOMENTUM_QUALITY_DISCOUNT_MULT", "0.85"))

# M1：Profile 权重表（composite_v5 = value，衍生分写 stock_score_profiles，禁止写 comprehensive_scores）
V5_PROFILE_WEIGHTS: dict = {
    "value": {  # = composite_v5 权威分权重（与 v5_scorer.V5_WEIGHTS 保持一致）
        "fundamental": 0.20, "quality": 0.15, "industry": 0.10,
        "capital": 0.15, "valuation": 0.10, "technical": 0.10,
        "market_env": 0.10, "policy": 0.05, "news": 0.03, "mood": 0.02,
    },
    "momentum": {  # 预期差/轮动视角：技术+资金+行业重，基本面/质量/估值轻
        "technical": 0.25, "capital": 0.22, "industry": 0.20,
        "fundamental": 0.08, "quality": 0.07, "valuation": 0.05,
        "market_env": 0.05, "policy": 0.04, "news": 0.02, "mood": 0.02,
    },
    "dividend": {  # 红利/防御视角：质量+估值+基本面重，技术/情绪轻
        "quality": 0.25, "valuation": 0.22, "fundamental": 0.20,
        "capital": 0.10, "industry": 0.08, "market_env": 0.05,
        "technical": 0.04, "policy": 0.03, "news": 0.02, "mood": 0.01,
    },
}

def latest_trading_date(db_path: str = None) -> str:
    """获取最近交易日"""
    import sqlite3
    path = db_path or DB_PATH
    row = sqlite3.connect(path).execute("SELECT MAX(trade_date) FROM stock_daily_quotes WHERE close IS NOT NULL").fetchone()
    return row[0] if row and row[0] else __import__('datetime').date.today().strftime("%Y-%m-%d")

# API 元信息
API_VERSION = "4.0"
API_TITLE = "AI 投研系统"
API_DESCRIPTION = "A股智能投研平台 v4.0"
FACTOR_BENCHMARK_DEFAULT = os.getenv("FACTOR_BENCHMARK_DEFAULT", "industry")
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8800"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "info")


def env_bool(key: str, default: bool = True) -> bool:
    v = os.getenv(key)
    if v is None:
        return default
    return v.lower() in ("1", "true", "yes", "on")


REGIME_VOL_EXPANSION = env_bool("AFR_REGIME_VOL_EXPANSION", True)
REGIME_TREND_PRIORITY_OVER_VOL = env_bool("AFR_REGIME_TREND_PRIORITY_OVER_VOL", True)
REGIME_PIPELINE_DAILY_REFRESH_MATRIX = env_bool("AFR_REGIME_DAILY_REFRESH_MATRIX", False)
REGIME_PIPELINE_WEEKLY_REFRESH_MATRIX = env_bool("AFR_REGIME_WEEKLY_REFRESH_MATRIX", True)

# 实验模块开关（默认开启，生产可关闭）
ENABLE_BACKTEST = env_bool("AFR_ENABLE_BACKTEST", True)
ENABLE_PORTFOLIO = env_bool("AFR_ENABLE_PORTFOLIO", True)
ENABLE_FACTOR_LAB = env_bool("AFR_ENABLE_FACTOR_LAB", True)
ENABLE_PREMIUM = env_bool("AFR_ENABLE_PREMIUM", True)
ENABLE_ADVANCED = env_bool("AFR_ENABLE_ADVANCED", True)
ENABLE_RAG = env_bool("AFR_ENABLE_RAG", True)

# 量化基础设施 Feature Flags（默认关闭新引擎，保持现网行为）
DATA_ENGINE = os.getenv("AFR_DATA_ENGINE", "sqlite")
BACKTEST_ENGINE = os.getenv("AFR_BACKTEST_ENGINE", "python")
USE_POLARS = env_bool("AFR_USE_POLARS", False)
FACTOR_MERGE_ENABLED = env_bool("AFR_FACTOR_MERGE_ENABLED", False)
FACTOR_NEUTRALIZE_ENABLED = env_bool("AFR_FACTOR_NEUTRALIZE", False)
FACTOR_EXPRESSION_ENABLED = env_bool("AFR_FACTOR_EXPRESSION_ENABLED", True)
FACTOR_GP_ENABLED = env_bool("AFR_FACTOR_GP_ENABLED", True)
SENTIMENT_WINDOW_DAYS = int(os.getenv("AFR_SENTIMENT_WINDOW_DAYS", "7"))
SYNC_REQUIRED_DIMENSIONS = [
    d.strip()
    for d in os.getenv(
        "AFR_SYNC_REQUIRED_DIMENSIONS",
        "fundamental_score,capital_score,policy_score,mood_score,val_score",
    ).split(",")
    if d.strip()
]
BATCH_FILL_MAX_CONCURRENT = int(os.getenv("AFR_BATCH_FILL_MAX_CONCURRENT", "1"))
P2_PARALLEL_ENABLED = env_bool("AFR_P2_PARALLEL", True)
JOB_STALE_TIMEOUT_MIN = int(os.getenv("AFR_JOB_STALE_TIMEOUT_MIN", "10"))
QLIB_ENABLED = env_bool("AFR_QLIB_ENABLED", False)
RUST_BACKTEST_APPROVED = env_bool("AFR_RUST_BACKTEST_APPROVED", False)
# ML 预测消费：未设置 env 时走 OOS RankIC 指标门控（见 services/ml_gate.py）
_qlib_pred_raw = os.getenv("AFR_QLIB_PREDICTIONS_APPROVED")
if _qlib_pred_raw is None or str(_qlib_pred_raw).strip() == "":
    QLIB_PREDICTIONS_APPROVED_FORCE = None  # type: bool | None
else:
    QLIB_PREDICTIONS_APPROVED_FORCE = env_bool("AFR_QLIB_PREDICTIONS_APPROVED", False)
# 兼容旧代码引用（仅表示 env 是否强制 true；实际放行请用 is_ml_predictions_approved()）
QLIB_PREDICTIONS_APPROVED = QLIB_PREDICTIONS_APPROVED_FORCE is True
ML_GATE_RECENT_FOLDS = int(os.getenv("AFR_ML_GATE_RECENT_FOLDS", "5"))
ML_GATE_MIN_FOLDS = int(os.getenv("AFR_ML_GATE_MIN_FOLDS", "3"))
ML_GATE_MIN_MEAN_RANK_IC = float(os.getenv("AFR_ML_GATE_MIN_RANK_IC", "0.02"))
_ml_gate_max_std = os.getenv("AFR_ML_GATE_MAX_RANK_IC_STD", "").strip()
ML_GATE_MAX_RANK_IC_STD = float(_ml_gate_max_std) if _ml_gate_max_std else None
# 全历史稳健性门控（近窗易被时序漂移/抽样运气骗）：
#   - 全历史均值 RankIC 下限：长期无排序能力（≈0）直接判否
#   - 前后折漂移上限：|后K折均值 - 前K折均值|，过大说明近窗不代表全历史
ML_GATE_MIN_FULL_MEAN_RANK_IC = float(os.getenv("AFR_ML_GATE_MIN_FULL_MEAN_RANK_IC", "0.01"))
_ml_gate_max_drift = os.getenv("AFR_ML_GATE_MAX_DRIFT", "0.05").strip()
ML_GATE_MAX_DRIFT = float(_ml_gate_max_drift) if _ml_gate_max_drift else None
ML_GATE_DRIFT_FOLDS = int(os.getenv("AFR_ML_GATE_DRIFT_FOLDS", "5"))
# ML 多 horizon 独立模型（方案 A）：5/20/60 日未来收益
ML_HORIZONS = tuple(
    int(x) for x in os.getenv("AFR_ML_HORIZONS", "5,20,60").split(",") if x.strip().isdigit()
) or (5, 20, 60)
ML_DEFAULT_HORIZON = int(os.getenv("AFR_ML_DEFAULT_HORIZON", "20"))

# 阶段Ⅱ灰度 — 双轨评分展示（5%→20%→50%→100%）
GRAY_RELEASE_PCT = int(os.getenv("AFR_GRAY_RELEASE_PCT", "20"))
DUAL_SCORE_UI = env_bool("AFR_DUAL_SCORE_UI", False)

BATCH_POLICY_USE_LLM = env_bool("AFR_BATCH_POLICY_USE_LLM", False)
ASHARE_FETCH_TIMEOUT_SEC = int(os.getenv("AFR_ASHARE_TIMEOUT_SEC", "25"))

# V5 事件 LLM 补漏（全局批处理 + 并发 + 标题缓存）
EVENT_LLM_BATCH_SIZE = int(os.getenv("AFR_EVENT_LLM_BATCH_SIZE", "20"))
EVENT_LLM_CONCURRENCY = int(os.getenv("AFR_EVENT_LLM_CONCURRENCY", "8"))
EVENT_LLM_SLEEP_MS = int(os.getenv("AFR_EVENT_LLM_SLEEP_MS", "0"))

# V5 调度：夜间抓取 / 收盘评分刷新
V5_NIGHTLY_FETCH_HOUR = int(os.getenv("AFR_V5_NIGHTLY_FETCH_HOUR", "2"))
V5_NIGHTLY_FETCH_MINUTE = int(os.getenv("AFR_V5_NIGHTLY_FETCH_MINUTE", "0"))
# 周日 03:00 周频：EPS/行业上修/v5_metrics/宏观/主力流
V5_WEEKLY_WEEKDAY = int(os.getenv("AFR_V5_WEEKLY_WEEKDAY", "6"))
V5_WEEKLY_HOUR = int(os.getenv("AFR_V5_WEEKLY_HOUR", "3"))
V5_WEEKLY_MINUTE = int(os.getenv("AFR_V5_WEEKLY_MINUTE", "0"))

# --- 新股票 onboard（见 docs/ONBOARDING_AND_DATA_SOURCE_PROPOSAL.md §11）---
# 单加 / onboard 完成后是否自动 batch-fill
AUTO_SCORE_ON_FETCH = env_bool("AFR_AUTO_SCORE_ON_FETCH", True)
# POST /stocks 完成后是否走 onboard 流水线（fetch + score）
ONBOARD_AUTO = env_bool("AFR_ONBOARD_AUTO", True)
# batch-fill 模式：新股票默认 compute_and_sync；运维可改 sync_only
ONBOARD_SCORE_MODE = os.getenv("AFR_ONBOARD_SCORE_MODE", "compute_and_sync").strip()
# batch-add 是否在入库后自动 onboard
BATCH_ADD_AUTO_ONBOARD = env_bool("AFR_BATCH_ADD_AUTO_ONBOARD", True)
# 超过此数量仅入库，响应 stock_ids，由前端确认后再 onboard
BATCH_ADD_AUTO_ONBOARD_MAX = int(os.getenv("AFR_BATCH_ADD_AUTO_ONBOARD_MAX", "5"))
# onboard / fetch-all 单股抓取线程池大小
FETCH_PARALLEL = int(os.getenv("AFR_FETCH_PARALLEL", "4"))
# 批量抓取并行度（默认 2，减轻 SQLite 写锁；可设 AFR_FETCH_ALL_PARALLEL=4）
FETCH_ALL_PARALLEL = int(os.getenv("AFR_FETCH_ALL_PARALLEL", "2"))
# 新股财报：True=ADATA/mootdx 快路径，scheduler 夜间补全量三表
FINANCE_FAST_PATH = env_bool("AFR_FINANCE_FAST_PATH", True)
FINANCE_FAST_QUOTE_DAYS = int(os.getenv("AFR_FINANCE_FAST_QUOTE_DAYS", "120"))
# akshare fallback 路径请求间隔（毫秒）
AKSHARE_SLEEP_MS = int(os.getenv("AFR_AKSHARE_SLEEP_MS", "500"))

# --- 批量抓取优化（见 docs/BATCH_FETCH_OPTIMIZATION_PROPOSAL.md）---
# 批量抓取默认模式：incremental | full
FETCH_DEFAULT_MODE = os.getenv("AFR_FETCH_DEFAULT_MODE", "incremental").strip().lower()
if FETCH_DEFAULT_MODE not in ("incremental", "full"):
    FETCH_DEFAULT_MODE = "incremental"
# 批量日常行情根数（full 仍用 DATA_FETCH_DAYS / 2000）
FETCH_ALL_QUOTE_DAYS = int(os.getenv("AFR_FETCH_ALL_QUOTE_DAYS", "500"))
# 批量跳过单股内因子（批末统一 calculate_all / calculate_incremental）
FETCH_ALL_SKIP_PER_STOCK_FACTOR = env_bool("AFR_FETCH_ALL_SKIP_PER_STOCK_FACTOR", True)
# 批量抓取完成后是否自动排队 V5 batch-fill（建议关，改手动重算）
FETCH_ALL_AUTO_SCORE = env_bool("AFR_FETCH_ALL_AUTO_SCORE", False)
# 增量模式：距上次成功全量财报超过 N 天才再拉 6 表
FINANCE_FULL_MAX_AGE_DAYS = int(os.getenv("AFR_FINANCE_FULL_MAX_AGE_DAYS", "30"))
# 东财令牌桶：每秒补充令牌数、突发容量
EASTMONEY_RATE_PER_SEC = float(os.getenv("AFR_EASTMONEY_RATE_PER_SEC", "2.0"))
EASTMONEY_RATE_BURST = int(os.getenv("AFR_EASTMONEY_RATE_BURST", "3"))
# 财报步骤连续失败 N 次后熔断跳过（本批剩余股票）
FETCH_CIRCUIT_BREAK_THRESHOLD = int(os.getenv("AFR_FETCH_CIRCUIT_BREAK_THRESHOLD", "5"))
GAP_STALE_DAYS = int(os.getenv("AFR_GAP_STALE_DAYS", "1"))
GAP_LOG_RETENTION_DAYS = int(os.getenv("AFR_GAP_LOG_RETENTION_DAYS", "90"))
GAP_LOG_ALERT_RETENTION_DAYS = int(os.getenv("AFR_GAP_LOG_ALERT_RETENTION_DAYS", "30"))
VENV_QUANT_PYTHON = os.getenv("AFR_VENV_QUANT_PYTHON", "")
QUANT_WORKERS_DIR = os.path.join(BASE_DIR, "workers")


def effective_backtest_engine(requested: str = "python") -> str:
    """Rust 需双条件：engine=rust 且 APPROVED=true"""
    if requested == "rust" and RUST_BACKTEST_APPROVED:
        return "rust"
    return "python"


# v3.0: 辩论配置块已删除（debate 链路在 P2 归档）。

# ── v3.0 V5-only 评分单轨 ──────────────────────────────────────────────────
# legacy  : 保留 composite_score / debate 写回（兼容期）
# v5_only : 仅 composite_v5 为权威分；不返回 legacy 字段
SCORING_MODE = os.getenv("AFR_SCORING_MODE", "legacy")

# Path A — 单股 fetch/onboard 完成后 inline 触发 V5 重算
V5_RECALC_INLINE_ON_SINGLE_FETCH = env_bool("AFR_V5_RECALC_INLINE", True)
# Path B — batch-fill / bulk sync 末尾统一触发一次 V5 batch 重算
V5_RECALC_AT_BATCH_END = env_bool("AFR_V5_RECALC_BATCH_END", True)
