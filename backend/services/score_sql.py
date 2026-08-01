"""评分相关 SQL 片段 — 统一最新 calc_date 查询，避免魔法数字阈值"""

import sqlite3

import config


def per_stock_latest_join(alias: str = "cs") -> str:
    """JOIN 子句：每只股票取 comprehensive_scores 最新一行。"""
    return f"""
        LEFT JOIN comprehensive_scores {alias} ON s.id = {alias}.stock_id
        AND {alias}.calc_date = (
            SELECT calc_date FROM comprehensive_scores c2
            WHERE c2.stock_id = s.id
            ORDER BY calc_date DESC LIMIT 1
        )
    """


def per_stock_latest_quality_join(alias: str = "cs") -> str:
    """JOIN：每只股票最新且 quality_score 非空的 comprehensive 行。

    八维同步可能在最新 calc_date 只写入 val_score 等局部字段，quality_score 仍为空；
    红利防御等策略依赖 quality，须跳过这类「半行」。
    """
    return f"""
        LEFT JOIN comprehensive_scores {alias} ON s.id = {alias}.stock_id
        AND {alias}.calc_date = (
            SELECT calc_date FROM comprehensive_scores c2
            WHERE c2.stock_id = s.id AND c2.quality_score IS NOT NULL
            ORDER BY calc_date DESC LIMIT 1
        )
    """


def per_stock_latest_v5_join(alias: str = "cs") -> str:
    """JOIN：展示用 V5 行（优先最新且 composite_v5 非空，避免八维同步新建空 V5 行覆盖）。"""
    return f"""
        LEFT JOIN comprehensive_scores {alias} ON s.id = {alias}.stock_id
        AND {alias}.calc_date = (
            SELECT calc_date FROM comprehensive_scores c2
            WHERE c2.stock_id = s.id
            ORDER BY
              CASE WHEN c2.composite_v5 IS NOT NULL THEN 0 ELSE 1 END,
              c2.calc_date DESC
            LIMIT 1
        )
    """


def per_stock_asof_join(alias: str, date_expr: str) -> str:
    """JOIN：每只股票在 date_expr（含）之前的最新 comprehensive 行。date_expr 可为 ? 或 SQL 表达式。"""
    return f"""
        LEFT JOIN comprehensive_scores {alias} ON s.id = {alias}.stock_id
        AND {alias}.calc_date = (
            SELECT calc_date FROM comprehensive_scores c2
            WHERE c2.stock_id = s.id AND c2.calc_date <= {date_expr}
            ORDER BY calc_date DESC LIMIT 1
        )
    """


def latest_batch_calc_date_subquery(min_ratio: float = 0.5) -> str:
    """返回覆盖足够多活跃股票的最近 calc_date（替代 HAVING COUNT >= 39）。"""
    pct = max(0.1, min(1.0, min_ratio))
    return f"""
        SELECT calc_date FROM comprehensive_scores
        WHERE composite_score IS NOT NULL
        GROUP BY calc_date
        HAVING COUNT(*) >= MAX(
            1,
            CAST((SELECT COUNT(*) FROM stocks WHERE is_active=1) * {pct} AS INTEGER)
        )
        ORDER BY calc_date DESC LIMIT 1
    """


def resolve_display_calc_date(conn: sqlite3.Connection, min_ratio: float = 0.5) -> str:
    """与 score_gap_scanner 对齐：优先选 7 维齐全率最高的 calc_date。"""
    from services.score_gap_scanner import scan_gaps

    active = conn.execute("SELECT COUNT(*) FROM stocks WHERE is_active=1").fetchone()[0]
    min_count = max(1, int(active * max(0.1, min(1.0, min_ratio))))

    candidates: list[str] = []
    trading = config.latest_trading_date()
    candidates.append(trading)

    batch_row = conn.execute(f"SELECT ({latest_batch_calc_date_subquery(min_ratio)})").fetchone()
    if batch_row and batch_row[0]:
        candidates.append(str(batch_row[0]))

    extra = conn.execute(
        """
        SELECT calc_date FROM comprehensive_scores
        WHERE composite_score IS NOT NULL
        GROUP BY calc_date
        HAVING COUNT(*) >= ?
        ORDER BY calc_date DESC LIMIT 3
        """,
        (min_count,),
    ).fetchall()
    for r in extra:
        d = str(r[0])
        if d not in candidates:
            candidates.append(d)

    best_date = trading
    best_rate = -1.0
    for d in candidates:
        report = scan_gaps(target_date=d)
        rate = float(report.get("sync_rate_all") or 0)
        if rate > best_rate or (rate == best_rate and d > best_date):
            best_rate = rate
            best_date = d
    return best_date
