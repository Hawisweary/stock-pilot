"""Beta 模块健康检查与数据契约 meta"""
from __future__ import annotations

import sqlite3
from datetime import date
from typing import List

from config import DB_PATH, latest_trading_date


def _issue(level: str, module: str, msg: str) -> dict:
    return {"level": level, "module": module, "msg": msg}


def get_rust_backtest_status() -> dict:
    """Rust 回测可用性 — 依赖非开源 qars3 + 环境开关。"""
    from config import RUST_BACKTEST_APPROVED

    try:
        import qars3

        qars3_installed = True
        qars3_version = getattr(qars3, "__version__", "unknown")
    except ImportError:
        qars3_installed = False
        qars3_version = None

    available = bool(qars3_installed and RUST_BACKTEST_APPROVED)
    if available:
        message = f"Rust 回测可用（qars3 {qars3_version}）"
    elif not qars3_installed:
        message = "Rust 回测未安装 qars3（非开源组件），已禁用，请使用 Python"
    elif not RUST_BACKTEST_APPROVED:
        message = "Rust 回测未开启（AFR_RUST_BACKTEST_APPROVED=false），请使用 Python"
    else:
        message = "Rust 回测不可用，请使用 Python"

    return {
        "qars3_installed": qars3_installed,
        "qars3_version": qars3_version,
        "approved": RUST_BACKTEST_APPROVED,
        "available": available,
        "engine_default": "python",
        "message": message,
    }


# beta-health 被 Layout 在每次页面加载调用,而内部聚合(COUNT(*)/DISTINCT
# 在千万行 factor_values 上)耗时 20s+,并阻塞事件循环、耗尽浏览器连接池,
# 曾导致全站请求超时、组合页空白。结果每日仅变一次。
# 策略:stale-while-revalidate —— 缓存过期也先返回旧值,后台线程刷新,
# 保证任何请求都不阻塞;仅首个请求(无任何缓存)会等待,已在启动时预热。
import threading as _threading

_health_cache: dict = {"data": None, "ts": 0.0}
_HEALTH_TTL = 600.0
_refresh_lock = _threading.Lock()
_refreshing = False


def _refresh_health_async() -> None:
    global _refreshing
    with _refresh_lock:
        if _refreshing:
            return
        _refreshing = True
    try:
        import time as _t

        result = _compute_beta_health()
        _health_cache["data"] = result
        _health_cache["ts"] = _t.time()
    except Exception:
        pass
    finally:
        _refreshing = False


def get_beta_health(force: bool = False) -> dict:
    import time as _t

    fresh = _health_cache["data"] is not None and _t.time() - _health_cache["ts"] < _HEALTH_TTL
    if fresh and not force:
        return _health_cache["data"]

    if _health_cache["data"] is not None and not force:
        # 有旧值:立即返回,后台刷新(绝不阻塞请求)
        _threading.Thread(target=_refresh_health_async, daemon=True).start()
        return _health_cache["data"]

    # 无任何缓存(首个请求/强制):同步计算一次
    result = _compute_beta_health()
    _health_cache["data"] = result
    _health_cache["ts"] = _t.time()
    return result


def warm_beta_health() -> None:
    """启动时后台预热,避免首个页面请求等待 20s+。"""
    _threading.Thread(target=lambda: get_beta_health(force=True), daemon=True).start()


def _compute_beta_health() -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    active = conn.execute("SELECT COUNT(*) FROM stocks WHERE is_active=1").fetchone()[0]
    latest_quote = conn.execute(
        "SELECT MAX(trade_date) FROM stock_daily_quotes WHERE close IS NOT NULL"
    ).fetchone()[0]
    latest_score = conn.execute("SELECT MAX(calc_date) FROM comprehensive_scores").fetchone()[0]
    score_days = conn.execute(
        "SELECT COUNT(DISTINCT calc_date) FROM comprehensive_scores"
    ).fetchone()[0]
    factor_days = 0
    try:
        factor_days = conn.execute(
            "SELECT COUNT(DISTINCT date) FROM factor_values"
        ).fetchone()[0]
    except sqlite3.OperationalError:
        pass

    trade_days = conn.execute(
        "SELECT COUNT(DISTINCT trade_date) FROM stock_daily_quotes"
    ).fetchone()[0]

    no_news = conn.execute(
        """SELECT COUNT(*) FROM stocks s WHERE s.is_active=1 AND NOT EXISTS (
            SELECT 1 FROM stock_news n WHERE n.stock_id=s.id)"""
    ).fetchone()[0]

    policy_flat = conn.execute(
        """SELECT COUNT(*) FROM comprehensive_scores cs
           WHERE cs.calc_date=(SELECT MAX(calc_date) FROM comprehensive_scores)
           AND cs.policy_score=50"""
    ).fetchone()[0]

    empty_industry = conn.execute(
        "SELECT COUNT(*) FROM stocks WHERE is_active=1 AND (industry_sw IS NULL OR industry_sw='')"
    ).fetchone()[0]

    conn.close()

    issues: List[dict] = []
    if score_days < 20:
        issues.append(_issue("warn", "backtest", f"评分历史仅{score_days}天，回测/IC 不稳定"))
    if factor_days < 10:
        issues.append(_issue("warn", "factors", f"因子历史仅{factor_days}天，IC 衰减分析受限"))
    if no_news > 0:
        issues.append(_issue("warn", "news", f"{no_news}只股票无新闻数据"))
    if policy_flat > active * 0.3:
        issues.append(_issue("warn", "policy", f"{policy_flat}只股票政策分=50，建议跑政策面+补行业"))
    if empty_industry > 0:
        issues.append(_issue("warn", "industry", f"{empty_industry}只股票 industry_sw 为空"))

    from services.upgrade_monitor import get_data_quality_metrics, get_migration_progress

    dq = get_data_quality_metrics()
    migration = get_migration_progress()
    issues.extend(dq.get("alerts", []))
    if not migration["gates"]["factor_merge_ready"]:
        issues.append(
            _issue(
                "warn",
                "migration",
                f"因子历史 {migration['factor_history_days']}/{migration['factor_history_target']} 天",
            )
        )

    backtest_ready = trade_days >= 60 and score_days >= 10
    ic_ready = score_days >= 15
    portfolio_ready = active >= 3 and latest_quote is not None

    from config import QLIB_ENABLED, USE_POLARS

    rust_backtest = get_rust_backtest_status()
    if not rust_backtest["available"]:
        issues.append(_issue("info", "rust_backtest", rust_backtest["message"]))

    return {
        "backtest_ready": backtest_ready,
        "ic_ready": ic_ready,
        "portfolio_ready": portfolio_ready,
        "rust_backtest": rust_backtest,
        "quant_flags": {
            "use_polars": USE_POLARS,
            "qlib_enabled": QLIB_ENABLED,
            "rust_backtest_approved": rust_backtest["approved"],
            "rust_backtest_available": rust_backtest["available"],
        },
        "universe_size": active,
        "latest_quote_date": latest_quote,
        "latest_score_date": latest_score,
        "score_history_days": score_days,
        "factor_history_days": factor_days,
        "trade_days": trade_days,
        "data_quality": {
            "industry_coverage_pct": dq["industry_coverage_pct"],
            "industry_coverage_ok": dq["industry_coverage_ok"],
            "interest_coverage_missing_pct": dq["interest_coverage_missing_pct"],
            "interest_coverage_ok": dq["interest_coverage_ok"],
        },
        "migration": {
            "factor_history_days": migration["factor_history_days"],
            "factor_history_target": migration["factor_history_target"],
            "factor_history_progress_pct": migration["factor_history_progress_pct"],
            "factor_merge_ready": migration["gates"]["factor_merge_ready"],
        },
        "issues": issues,
        "checked_at": date.today().isoformat(),
    }


def attach_meta(result: dict) -> dict:
    """为 Beta API 响应附加 meta"""
    if not isinstance(result, dict) or "error" in result:
        return result
    health = get_beta_health()
    result["meta"] = {
        "data_as_of": health.get("latest_score_date") or health.get("latest_quote_date"),
        "universe_size": health["universe_size"],
        "score_history_days": health["score_history_days"],
        "factor_history_days": health["factor_history_days"],
        "warnings": [i["msg"] for i in health.get("issues", []) if i["level"] in ("warn", "error")],
    }
    return result
