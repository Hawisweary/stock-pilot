"""财务指标补全 — 利息保障倍数等（方案书 §1 技术风险）"""
from __future__ import annotations

import sqlite3

from config import DB_PATH


def _ensure_ic_column(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(financial_indicators)").fetchall()}
    if "interest_coverage_ratio" not in cols:
        conn.execute("ALTER TABLE financial_indicators ADD COLUMN interest_coverage_ratio REAL")
        conn.commit()


def backfill_interest_coverage(db_path: str = None) -> dict:
    """
    用最新年报 operating_profit 与融资现金流估算利息保障倍数。
    无精确利息费用时：interest_est = max(|financing_cf| * 0.25, total_liabilities * 0.02)
    """
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    _ensure_ic_column(conn)

    stocks = conn.execute("SELECT id, code FROM stocks WHERE is_active=1").fetchall()
    updated = 0
    skipped = 0
    samples = []

    for s in stocks:
        sid = s["id"]
        fi = conn.execute(
            """SELECT id, calc_date FROM financial_indicators
               WHERE stock_id=? ORDER BY calc_date DESC LIMIT 1""",
            (sid,),
        ).fetchone()
        if not fi:
            skipped += 1
            continue

        fr = conn.execute(
            """SELECT operating_profit, financing_cf, total_liabilities
               FROM financial_reports
               WHERE stock_id=? AND report_type='annual'
               ORDER BY period_end_date DESC LIMIT 1""",
            (sid,),
        ).fetchone()

        ic_ratio = None
        method = "none"
        if fr and fr["operating_profit"] is not None:
            op = float(fr["operating_profit"])
            fin_cf = abs(float(fr["financing_cf"] or 0))
            liab = float(fr["total_liabilities"] or 0)
            interest_est = max(fin_cf * 0.25, liab * 0.02, 1.0)
            if op > 0:
                ic_ratio = round(op / interest_est, 4)
                method = "operating_profit_est"
            elif op <= 0:
                ic_ratio = 0.0
                method = "negative_ebit"

        if ic_ratio is None:
            skipped += 1
            continue

        conn.execute(
            "UPDATE financial_indicators SET interest_coverage_ratio=? WHERE id=?",
            (ic_ratio, fi["id"]),
        )
        updated += 1
        if len(samples) < 5:
            samples.append({"code": s["code"], "ic_ratio": ic_ratio, "method": method})

    conn.commit()
    conn.close()
    return {
        "updated": updated,
        "skipped": skipped,
        "total": len(stocks),
        "coverage_pct": round(updated / max(len(stocks), 1) * 100, 2),
        "samples": samples,
    }
