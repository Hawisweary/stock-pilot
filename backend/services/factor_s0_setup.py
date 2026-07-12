"""S0 因子数据质量 — 一次性初始化流水线"""
from __future__ import annotations

import sqlite3

from config import DB_PATH
from services.data_cleaner import backfill_adj_close
from services.factor_quality import ensure_quality_prerequisites
from services.factor_values_wide import migrate_eav_to_wide
from services.stock_lifecycle import sync_lifecycle_from_stocks


def run_factor_s0_setup(migrate_wide: bool = True) -> dict:
    """adj_close 回填、生命周期、披露日历、EAV→宽表迁移。"""
    conn = sqlite3.connect(DB_PATH)
    adj = backfill_adj_close(conn)
    life = sync_lifecycle_from_stocks(conn)
    conn.commit()
    conn.close()

    cal = ensure_quality_prerequisites()
    wide = migrate_eav_to_wide() if migrate_wide else {"skipped": True}

    return {
        "adj_close": adj,
        "lifecycle": life,
        "financial_calendar": cal.get("financial_calendar", cal),
        "wide_migration": wide,
    }
