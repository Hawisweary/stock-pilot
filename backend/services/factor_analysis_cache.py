"""因子IC分析结果缓存(cache.db持久化) — 单次全量重算约13秒,按数据日期缓存+每日预热"""
from __future__ import annotations

import json
import logging
import sqlite3

from config import DB_PATH

logger = logging.getLogger(__name__)


def latest_factor_date(factor_id: str) -> str:
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT MAX(date) FROM factor_values WHERE factor_id=?", (factor_id,)
        ).fetchone()
        return row[0] or ""
    finally:
        conn.close()


def latest_any_factor_date() -> str:
    """全表最新因子数据日期(用于 IC汇总/热力图/相关矩阵 这类跨因子分析的缓存失效键)。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute("SELECT MAX(date) FROM factor_values").fetchone()
        return row[0] or ""
    finally:
        conn.close()


def cached_by_date(key: str, compute_fn, *, allow_inprocess: bool = True) -> dict:
    """通用:按'全市场最新因子数据日期'缓存跨因子的重计算(IC汇总/热力图/相关矩阵)。

    复用 factor_analysis_cache 表:factor_id=key, forward_days=0。数据日期不变直接命中。
    allow_inprocess=False 时,缓存未命中不在本进程计算(IC 计算 CPU 密集会因 GIL
    冻结 API),而是返回 {"pending": True} 由子进程(启动/每日预热)填充,避免阻塞。
    计算结果为空或含 error 时不落缓存。
    """
    data_date = latest_any_factor_date()
    hit = get_cached(key, 0, data_date)
    if hit is not None:
        return hit
    if not allow_inprocess:
        return {"pending": True, "message": "分析正在后台计算，请稍后刷新", "data_date": data_date}
    result = compute_fn()
    if isinstance(result, dict) and not result.get("error"):
        store(key, 0, data_date, result)
    return result


def get_cached(factor_id: str, forward_days: int, data_date: str) -> dict | None:
    """数据日期一致才命中。"""
    from database import cache_connect

    if not data_date:
        return None
    conn = cache_connect()
    try:
        row = conn.execute(
            """SELECT result_json FROM factor_analysis_cache
               WHERE factor_id=? AND forward_days=? AND data_date=?""",
            (factor_id, forward_days, data_date),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except Exception:
        return None


def store(factor_id: str, forward_days: int, data_date: str, result: dict) -> None:
    from database import cache_connect

    if not data_date or not isinstance(result, dict) or result.get("error"):
        return
    conn = cache_connect()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO factor_analysis_cache
               (factor_id, forward_days, data_date, result_json, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'))""",
            (factor_id, forward_days, data_date, json.dumps(result, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


def compute_and_cache(factor_id: str, forward_days: int = 20) -> dict:
    """算一次并落缓存(API与预热共用入口)。"""
    from services.factor_factory import factor_extended_analysis

    data_date = latest_factor_date(factor_id)
    cached = get_cached(factor_id, forward_days, data_date)
    if cached is not None:
        return cached
    result = factor_extended_analysis(factor_id, forward_days=forward_days)
    store(factor_id, forward_days, data_date, result)
    return result


def _health_status(mean_ic, ir, sig) -> str:
    """由 IC/IR/显著性判定因子健康度: strong(有效) / weak(边际) / decayed(失效)。"""
    if mean_ic is None:
        return "unknown"
    mic = abs(float(mean_ic))
    ric = abs(float(ir or 0))
    stars = (sig or {}).get("significance", "") if isinstance(sig, dict) else ""
    significant = bool(stars) and stars in ("*", "**", "***")
    if not significant and mic < 0.015:
        return "decayed"       # 无显著性且IC接近0 → 失效
    if ric < 0.15 or (not significant) or mic < 0.02:
        return "weak"          # IR低/边际显著 → 减弱
    return "strong"


def factor_health_all(forward_days: int = 20) -> dict:
    """全部注册因子的健康度(读缓存,快)。用于因子库标色/衰减告警。"""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        fids = [r[0] for r in conn.execute("SELECT factor_id FROM factor_registry ORDER BY factor_id")]
    finally:
        conn.close()
    out = {}
    n_decayed = 0
    for fid in fids:
        r = get_cached(fid, forward_days, latest_factor_date(fid))
        if not r or r.get("error"):
            out[fid] = {"status": "unknown", "mean_ic": None, "ir": None}
            continue
        st = _health_status(r.get("mean_ic"), r.get("ir"), r.get("ic_significance"))
        if st == "decayed":
            n_decayed += 1
        out[fid] = {
            "status": st,
            "mean_ic": r.get("mean_ic"),
            "ir": r.get("ir"),
            "significance": (r.get("ic_significance") or {}).get("significance"),
        }
    return {"factors": out, "decayed_count": n_decayed, "total": len(fids)}


def warm_ic_tabs(forward_days: int = 20) -> None:
    """只预热 IC tab 的三个慢端点(IC汇总/热力图/相关矩阵),启动时后台调用。"""
    from services.custom_factor import factor_correlation_matrix
    from services.ic_engine import analyze_all_score_factors, analyze_ic_heatmap

    for key, fn in (
        (f"ic:all:60:{forward_days}", lambda: analyze_all_score_factors(forward_days=forward_days, period=60)),
        ("ic:heatmap:60", lambda: analyze_ic_heatmap(period=60)),
        ("factor:correlation", factor_correlation_matrix),
    ):
        try:
            cached_by_date(key, fn)
        except Exception as e:
            logger.warning("IC tab 预热失败 %s: %s", key, e)


def warm_all(forward_days: int = 20) -> dict:
    """预热全部注册因子的默认分析(每日流水线末尾调用)。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        fids = [
            r[0]
            for r in conn.execute(
                "SELECT factor_id FROM factor_registry ORDER BY factor_id"
            ).fetchall()
        ]
    finally:
        conn.close()

    warmed = 0
    errors = 0
    for fid in fids:
        try:
            compute_and_cache(fid, forward_days)
            warmed += 1
        except Exception as e:
            errors += 1
            logger.warning("因子分析预热失败 %s: %s", fid, e)

    # 预热 IC tab 的跨因子分析(IC汇总/热力图/相关矩阵),这些单次 12~45s
    ic_warmed = 0
    try:
        from services.custom_factor import factor_correlation_matrix
        from services.ic_engine import analyze_all_score_factors, analyze_ic_heatmap

        for key, fn in (
            (f"ic:all:60:{forward_days}", lambda: analyze_all_score_factors(forward_days=forward_days, period=60)),
            ("ic:heatmap:60", lambda: analyze_ic_heatmap(period=60)),
            ("factor:correlation", factor_correlation_matrix),
        ):
            try:
                cached_by_date(key, fn)
                ic_warmed += 1
            except Exception as e:
                logger.warning("IC分析预热失败 %s: %s", key, e)
    except Exception as e:
        logger.warning("IC分析预热跳过: %s", e)

    return {"warmed": warmed, "errors": errors, "total": len(fids), "ic_warmed": ic_warmed}
