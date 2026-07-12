"""
分数变动预警服务 (PC-1.0 P0-2)

在 V5 batch 写入 comprehensive_scores 之后调用 record_score_changes()，
对比前次最新分数，将 |delta| >= threshold 的变动写入 score_change_log。
幂等：按 (stock_id, calc_date) UNIQUE 去重，重复调用安全。
"""
from __future__ import annotations

import sqlite3
import logging
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 5.0   # |delta| 低于此值不记录


def record_score_changes(
    conn: sqlite3.Connection,
    stock_ids: Optional[list[int]] = None,
    threshold: float = DEFAULT_THRESHOLD,
) -> list[dict]:
    """
    对比每只股票最新两条评分，将变动写入 score_change_log。
    返回本次写入的变动列表（用于 API 直接返回 Toast 数据）。
    """
    if stock_ids:
        ph = ",".join("?" * len(stock_ids))
        rows = conn.execute(
            f"""
            SELECT stock_id, calc_date, composite_v5, veto_status,
                   ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY calc_date DESC) AS rn
            FROM comprehensive_scores
            WHERE stock_id IN ({ph}) AND composite_v5 IS NOT NULL
            """,
            stock_ids,
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT stock_id, calc_date, composite_v5, veto_status,
                   ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY calc_date DESC) AS rn
            FROM comprehensive_scores
            WHERE composite_v5 IS NOT NULL
            """
        ).fetchall()

    # 按 stock_id 聚合最新两条
    latest: dict[int, dict] = {}
    prev: dict[int, dict] = {}
    for r in rows:
        sid, calc_date, score, veto, rn = r
        if rn == 1:
            latest[sid] = {"calc_date": calc_date, "score": score, "veto": veto}
        elif rn == 2:
            prev[sid] = {"calc_date": calc_date, "score": score, "veto": veto}

    changes = []
    for sid, cur in latest.items():
        old = prev.get(sid)
        old_score = old["score"] if old else None
        delta = cur["score"] - (old_score or cur["score"])
        old_veto = old["veto"] if old else None
        veto_changed = int(old_veto != cur["veto"]) if old else 0

        if abs(delta) < threshold and not veto_changed:
            continue

        try:
            conn.execute(
                """
                INSERT INTO score_change_log
                    (stock_id, calc_date, old_score, new_score, delta, old_veto, new_veto, veto_changed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(stock_id, calc_date) DO NOTHING
                """,
                (sid, cur["calc_date"], old_score, cur["score"], delta,
                 old_veto, cur["veto"], veto_changed),
            )
            if conn.execute(
                "SELECT changes()"
            ).fetchone()[0] > 0:
                changes.append({
                    "stock_id": sid,
                    "calc_date": cur["calc_date"],
                    "old_score": old_score,
                    "new_score": cur["score"],
                    "delta": delta,
                    "old_veto": old_veto,
                    "new_veto": cur["veto"],
                    "veto_changed": bool(veto_changed),
                })
        except Exception as exc:
            logger.warning("score_alert insert failed for stock %s: %s", sid, exc)

    conn.commit()
    logger.info("score_alert: %d 条变动写入 score_change_log", len(changes))
    return changes
