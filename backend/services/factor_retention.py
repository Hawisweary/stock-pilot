"""factor_values 历史保留 — 防止只增不减无限膨胀。

factor_values 每交易日约 20 万行,无保留会一年涨到 5000 万行 / 4GB+。
按"保留最近 N 个交易日"裁剪(IC 分析默认只用 60-90 天,回测也足够)。
删除按日期逐批提交,写锁每次只持有一天的量、亚秒级,不阻塞 API。
删除后 SQLite 空闲页会被后续写入复用,文件大小趋于平稳(无需 VACUUM)。
"""
from __future__ import annotations

import logging
import os
import sqlite3

from config import DB_PATH

logger = logging.getLogger(__name__)

_DEFAULT_KEEP_DAYS = int(os.getenv("AFR_FACTOR_RETENTION_DAYS", "250"))


def prune_factor_values(keep_days: int | None = None) -> dict:
    """裁剪 factor_values / factor_values_wide,只保留最近 keep_days 个交易日。"""
    keep_days = keep_days or _DEFAULT_KEEP_DAYS
    conn = sqlite3.connect(DB_PATH, timeout=120)
    try:
        conn.execute("PRAGMA busy_timeout=120000")
        # 第 keep_days 新的日期即截断点,早于它的删除
        cutoff = conn.execute(
            """SELECT date FROM (
                   SELECT DISTINCT date FROM factor_values ORDER BY date DESC LIMIT ?
               ) ORDER BY date ASC LIMIT 1""",
            (keep_days,),
        ).fetchone()
        if not cutoff or not cutoff[0]:
            return {"pruned": 0, "reason": "数据不足或无需裁剪", "keep_days": keep_days}
        cutoff_date = cutoff[0]

        old_dates = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT date FROM factor_values WHERE date < ? ORDER BY date",
                (cutoff_date,),
            ).fetchall()
        ]
        if not old_dates:
            return {"pruned": 0, "cutoff": cutoff_date, "keep_days": keep_days}

        total = 0
        for d in old_dates:  # 逐日删除+提交,写锁只按天短持有
            cur = conn.execute("DELETE FROM factor_values WHERE date=?", (d,))
            total += cur.rowcount
            try:
                conn.execute("DELETE FROM factor_values_wide WHERE date=?", (d,))
            except sqlite3.OperationalError:
                pass
            conn.commit()
        logger.info(
            "[Retention] factor_values 裁剪 %d 天 %d 行 (cutoff=%s, keep=%d)",
            len(old_dates), total, cutoff_date, keep_days,
        )
        return {"pruned": total, "days_removed": len(old_dates), "cutoff": cutoff_date, "keep_days": keep_days}
    finally:
        conn.close()
