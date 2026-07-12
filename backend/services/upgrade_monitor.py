"""升级监控 — 行业覆盖率、利息保障缺失率、迁移进度（方案书 §4.1）"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import date
from typing import List, Optional

from config import DATA_DIR, DB_PATH

# 方案书阈值
INDUSTRY_COVERAGE_MIN = 98.0  # %
INTEREST_COVERAGE_MISSING_MAX = 5.0  # %
FACTOR_HISTORY_TARGET = 60  # days


def _issue(level: str, module: str, msg: str, metric: str = "") -> dict:
    return {"level": level, "module": module, "msg": msg, "metric": metric}


def get_data_quality_metrics(db_path: str = None) -> dict:
    from config import DB_READ_PATH
    import os

    path = db_path or DB_PATH
    if not db_path and os.path.isfile(DB_READ_PATH):
        path = DB_READ_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    active = conn.execute("SELECT COUNT(*) FROM stocks WHERE is_active=1").fetchone()[0]
    with_industry = conn.execute(
        """SELECT COUNT(*) FROM stocks WHERE is_active=1
           AND industry_sw IS NOT NULL AND industry_sw != ''"""
    ).fetchone()[0]
    industry_coverage_pct = round(with_industry / max(active, 1) * 100, 2)

    missing_industry = conn.execute(
        """SELECT code, name FROM stocks WHERE is_active=1
           AND (industry_sw IS NULL OR industry_sw='') LIMIT 20"""
    ).fetchall()

    # 每只活跃股取最新 financial_indicators
    ic_missing = 0
    ic_total = 0
    ic_missing_codes: List[str] = []
    has_ic_col = "interest_coverage_ratio" in {
        r[1] for r in conn.execute("PRAGMA table_info(financial_indicators)").fetchall()
    }
    stocks = conn.execute("SELECT id, code FROM stocks WHERE is_active=1").fetchall()
    for s in stocks:
        ic_total += 1
        if not has_ic_col:
            ic_missing += 1
            if len(ic_missing_codes) < 20:
                ic_missing_codes.append(s["code"])
            continue
        row = conn.execute(
            """SELECT interest_coverage_ratio FROM financial_indicators
               WHERE stock_id=? ORDER BY calc_date DESC LIMIT 1""",
            (s["id"],),
        ).fetchone()
        if not row or row["interest_coverage_ratio"] is None:
            ic_missing += 1
            if len(ic_missing_codes) < 20:
                ic_missing_codes.append(s["code"])

    ic_missing_pct = round(ic_missing / max(ic_total, 1) * 100, 2)
    conn.close()

    alerts: List[dict] = []
    if industry_coverage_pct < INDUSTRY_COVERAGE_MIN:
        alerts.append(
            _issue(
                "error" if industry_coverage_pct < 95 else "warn",
                "industry",
                f"行业分类覆盖率 {industry_coverage_pct}% < {INDUSTRY_COVERAGE_MIN}%",
                "industry_coverage_pct",
            )
        )
    if ic_missing_pct > INTEREST_COVERAGE_MISSING_MAX:
        alerts.append(
            _issue(
                "warn",
                "financial",
                f"利息保障倍数缺失率 {ic_missing_pct}% > {INTEREST_COVERAGE_MISSING_MAX}%",
                "interest_coverage_missing_pct",
            )
        )

    return {
        "active_stocks": active,
        "industry_coverage_pct": industry_coverage_pct,
        "industry_coverage_threshold": INDUSTRY_COVERAGE_MIN,
        "industry_coverage_ok": industry_coverage_pct >= INDUSTRY_COVERAGE_MIN,
        "missing_industry_samples": [dict(r) for r in missing_industry],
        "interest_coverage_missing_pct": ic_missing_pct,
        "interest_coverage_missing_threshold": INTEREST_COVERAGE_MISSING_MAX,
        "interest_coverage_ok": ic_missing_pct <= INTEREST_COVERAGE_MISSING_MAX if has_ic_col else False,
        "interest_coverage_column_present": has_ic_col,
        "interest_coverage_missing_samples": ic_missing_codes,
        "alerts": alerts,
        "checked_at": date.today().isoformat(),
    }


def get_migration_progress(db_path: str = None) -> dict:
    from config import DB_READ_PATH
    import os

    path = db_path or DB_PATH
    if not db_path and os.path.isfile(DB_READ_PATH):
        path = DB_READ_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    score_days = conn.execute(
        "SELECT COUNT(DISTINCT calc_date) FROM comprehensive_scores"
    ).fetchone()[0]
    factor_days = 0
    factor_rows = 0
    try:
        factor_days = conn.execute(
            "SELECT COUNT(DISTINCT date) FROM factor_values"
        ).fetchone()[0]
        factor_rows = conn.execute("SELECT COUNT(*) FROM factor_values").fetchone()[0]
    except sqlite3.OperationalError:
        pass

    active = conn.execute("SELECT COUNT(*) FROM stocks WHERE is_active=1").fetchone()[0]

    jobs_summary = {"pending": 0, "running": 0, "done": 0, "failed": 0, "recent": []}
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS job_runs (
                id TEXT PRIMARY KEY, job_type TEXT, payload_json TEXT, status TEXT,
                result_json TEXT, error TEXT, created_at TEXT, started_at TEXT, finished_at TEXT
            )"""
        )
        for status in ("pending", "running", "done", "failed"):
            jobs_summary[status] = conn.execute(
                "SELECT COUNT(*) FROM job_runs WHERE status=?", (status,)
            ).fetchone()[0]
        recent = conn.execute(
            """SELECT id, job_type, status, created_at, finished_at, error
               FROM job_runs ORDER BY created_at DESC LIMIT 10"""
        ).fetchall()
        jobs_summary["recent"] = [dict(r) for r in recent]
    except sqlite3.OperationalError:
        pass

    # 内存队列（若 API 进程内）
    try:
        from services.job_queue import list_jobs

        mem_jobs = list_jobs(5)
        jobs_summary["in_memory"] = [
            {"id": j.id, "type": j.job_type, "status": j.status.value} for j in mem_jobs
        ]
    except Exception:
        jobs_summary["in_memory"] = []

    conn.close()

    factor_pct = round(min(factor_days / FACTOR_HISTORY_TARGET, 1.0) * 100, 1)
    ic_stable = False
    try:
        from services.ic_stability import is_ic_stable

        ic_stable = is_ic_stable()
    except Exception:
        ic_stable = score_days >= 20

    manifest_path = os.path.join(DATA_DIR, "pilot_manifest.json")
    pilot = {"enabled": False, "stock_count": 0}
    if os.path.isfile(manifest_path):
        with open(manifest_path, encoding="utf-8") as f:
            m = json.load(f)
        pilot = {
            "enabled": True,
            "stock_count": len(m.get("stocks", [])),
            "created_at": m.get("created_at"),
            "pilot_db": m.get("pilot_db"),
        }

    return {
        "score_history_days": score_days,
        "factor_history_days": factor_days,
        "factor_history_target": FACTOR_HISTORY_TARGET,
        "factor_history_progress_pct": factor_pct,
        "factor_values_rows": factor_rows,
        "active_stocks": active,
        "jobs": jobs_summary,
        "pilot": pilot,
        "gates": {
            "factor_merge_ready": factor_days >= FACTOR_HISTORY_TARGET,
            "ic_stable_ready": ic_stable,
        },
        "checked_at": date.today().isoformat(),
    }


def get_upgrade_dashboard(db_path: str = None) -> dict:
    """合并数据质量 + 迁移进度，供 /api/system/upgrade-metrics"""
    quality = get_data_quality_metrics(db_path)
    migration = get_migration_progress(db_path)
    all_alerts = quality.get("alerts", [])
    if not migration["gates"]["factor_merge_ready"]:
        all_alerts.append(
            _issue(
                "warn",
                "migration",
                f"因子历史 {migration['factor_history_days']}/{FACTOR_HISTORY_TARGET} 天，合成因子未就绪",
                "factor_history_days",
            )
        )
    return {
        "data_quality": quality,
        "migration": migration,
        "alerts": all_alerts,
        "all_ok": quality["industry_coverage_ok"]
        and quality["interest_coverage_ok"]
        and not any(a["level"] == "error" for a in all_alerts),
        "checked_at": date.today().isoformat(),
    }
