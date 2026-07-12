"""综合评分持久化 — 统一 calc_date、upsert 各维度分、读取时回退维度表"""
from __future__ import annotations

import sqlite3
from typing import Any

import config
from config import latest_trading_date
from database import write_lock

DIMENSION_COLUMNS = {
    "capital": "capital_score",
    "policy": "policy_score",
    "sentiment": "mood_score",
    "technical": "technical_score",
}

DIMENSION_TABLES: dict[str, tuple[str, str, str]] = {
    "capital_score": ("capital_scores", "composite_score", "date"),
    "policy_score": ("policy_scores", "composite_score", "date"),
    "mood_score": ("sentiment_scores", "composite_score", "date"),
    "technical_score": ("tech_analysis_cache", "score", "created_at"),
    "fundamental_score": ("factor_scores", "composite_score", "calc_date"),
    "val_score": ("valuation_scores", "composite_score", "date"),
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=120)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _retry_locked(fn, *, attempts: int = 15):
    import time

    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            last = e
            if "locked" not in str(e).lower() or i == attempts - 1:
                raise
            time.sleep(min(2.0, 0.2 * (i + 1)))
    if last:
        raise last


def resolve_calc_date(conn: sqlite3.Connection, stock_id: int) -> str:
    row = conn.execute(
        "SELECT calc_date FROM comprehensive_scores WHERE stock_id=? ORDER BY calc_date DESC LIMIT 1",
        (stock_id,),
    ).fetchone()
    if row and row[0]:
        return str(row[0])
    return latest_trading_date()


def load_latest_comprehensive_rows(
    conn: sqlite3.Connection, stock_ids: list[int]
) -> dict[int, dict[str, Any]]:
    """每只股票取 comprehensive_scores 最新一行（含 calc_date、composite_score）。"""
    if not stock_ids:
        return {}
    conn.row_factory = sqlite3.Row
    placeholders = ",".join(["?"] * len(stock_ids))
    cols = [
        "fundamental_score",
        "technical_score",
        "sentiment_score",
        "capital_score",
        "policy_score",
        "mood_score",
        "val_score",
    ]
    col_sql = ", ".join(cols)
    rows = conn.execute(
        f"""
        SELECT cs.stock_id, cs.calc_date, cs.composite_score, {col_sql}
        FROM comprehensive_scores cs
        INNER JOIN (
            SELECT stock_id, MAX(calc_date) AS md
            FROM comprehensive_scores
            WHERE stock_id IN ({placeholders})
            GROUP BY stock_id
        ) t ON cs.stock_id = t.stock_id AND cs.calc_date = t.md
        """,
        tuple(stock_ids),
    ).fetchall()
    return {int(r["stock_id"]): dict(r) for r in rows}


def recompute_composite(conn: sqlite3.Connection, stock_id: int, calc_date: str) -> None:
    # v3.0: DEPRECATED — 八维聚合分已停止写入，此函数保留签名供旧调用方兼容，不执行任何操作。
    # 综合分由 v5_scorer.compute_all_v5_scores / get_stock_v5_score 写入 composite_v5。
    return


def upsert_dimension_score(
    stock_id: int,
    column: str,
    value: float | int | None,
    *,
    calc_date: str | None = None,
) -> str:
    allowed = {
        *DIMENSION_COLUMNS.values(),
        "fundamental_score",
        "technical_score",
        "sentiment_score",
        "val_score",
        "risk_score",
        # composite_score 已废弃（v3.0），不再允许外部写入
    }
    if column not in allowed:
        raise ValueError(f"未知列: {column}")

    with write_lock:
        def _do() -> str:
            conn = _connect()
            try:
                date = calc_date or resolve_calc_date(conn, stock_id)
                existing = conn.execute(
                    "SELECT id FROM comprehensive_scores WHERE stock_id=? AND calc_date=?",
                    (stock_id, date),
                ).fetchone()
                if existing:
                    conn.execute(
                        f"UPDATE comprehensive_scores SET {column}=? WHERE stock_id=? AND calc_date=?",
                        (value, stock_id, date),
                    )
                else:
                    conn.execute(
                        f"INSERT INTO comprehensive_scores (stock_id, calc_date, {column}) VALUES (?, ?, ?)",
                        (stock_id, date, value),
                    )
                conn.commit()
                return date
            finally:
                conn.close()

        return _retry_locked(_do)


def upsert_dimension_scores_batch(
    updates: dict[int, dict[str, float | int]],
    calc_date: str,
) -> list[int]:
    """多股多列批量 upsert（单事务）。返回 changed stock_id 列表供 Path B V5 重算。

    v3.0: 不再调用 recompute_composite；调用方负责 batch V5 重算。
    """
    if not updates:
        return []
    allowed = {
        *DIMENSION_COLUMNS.values(),
        "fundamental_score",
        "technical_score",
        "sentiment_score",
        "val_score",
        "risk_score",
        "quality_score",
        "industry_score",
        "capital_score",
        "mood_score",
        "market_env_score",
        "policy_score",
        # composite_score 已废弃（v3.0），禁止批量写入
    }

    with write_lock:
        def _do() -> list[int]:
            conn = _connect()
            affected: set[int] = set()
            try:
                for stock_id, cols in updates.items():
                    if not cols:
                        continue
                    for col in cols:
                        if col not in allowed:
                            raise ValueError(f"未知列: {col}")

                    row = conn.execute(
                        "SELECT * FROM comprehensive_scores WHERE stock_id=? AND calc_date=?",
                        (stock_id, calc_date),
                    ).fetchone()

                    if row:
                        set_clause = ", ".join(f"{col}=?" for col in cols)
                        conn.execute(
                            f"UPDATE comprehensive_scores SET {set_clause} WHERE stock_id=? AND calc_date=?",
                            (*cols.values(), stock_id, calc_date),
                        )
                    else:
                        col_names = ["stock_id", "calc_date", *cols.keys()]
                        placeholders = ", ".join(["?"] * len(col_names))
                        conn.execute(
                            f"INSERT INTO comprehensive_scores ({', '.join(col_names)}) VALUES ({placeholders})",
                            (stock_id, calc_date, *cols.values()),
                        )
                    affected.add(stock_id)

                conn.commit()
                return sorted(affected)
            finally:
                conn.close()

        return _retry_locked(_do, attempts=15)


def upsert_dimension_scores_to_latest_rows(
    updates: dict[int, dict[str, float | int]],
) -> int:
    """多股多列写入各自最新 comprehensive 行（解决 onboard 新 calc_date 缺维度分）。

    v3.0: 不再调用 recompute_composite；调用方负责后续 V5 重算。
    """
    if not updates:
        return 0
    allowed = {
        *DIMENSION_COLUMNS.values(),
        "fundamental_score",
        "technical_score",
        "sentiment_score",
        "val_score",
        "risk_score",
        "quality_score",
        "industry_score",
        "capital_score",
        "mood_score",
        "market_env_score",
        "policy_score",
        # composite_score 已废弃（v3.0），禁止写入
    }

    with write_lock:
        def _do() -> int:
            conn = _connect()
            affected: set[tuple[int, str]] = set()
            try:
                for stock_id, cols in updates.items():
                    if not cols:
                        continue
                    for col in cols:
                        if col not in allowed:
                            raise ValueError(f"未知列: {col}")
                    date = resolve_calc_date(conn, stock_id)
                    row = conn.execute(
                        "SELECT * FROM comprehensive_scores WHERE stock_id=? AND calc_date=?",
                        (stock_id, date),
                    ).fetchone()
                    if row:
                        set_clause = ", ".join(f"{col}=?" for col in cols)
                        conn.execute(
                            f"UPDATE comprehensive_scores SET {set_clause} WHERE stock_id=? AND calc_date=?",
                            (*cols.values(), stock_id, date),
                        )
                    else:
                        col_names = ["stock_id", "calc_date", *cols.keys()]
                        placeholders = ", ".join(["?"] * len(col_names))
                        conn.execute(
                            f"INSERT INTO comprehensive_scores ({', '.join(col_names)}) VALUES ({placeholders})",
                            (stock_id, date, *cols.values()),
                        )
                    affected.add((stock_id, date))

                conn.commit()
                return len({sid for sid, _ in affected})
            finally:
                conn.close()

        return _retry_locked(_do, attempts=15)


def upsert_dimension_by_key(stock_id: int, dimension: str, value: float | int | None) -> str:
    col = DIMENSION_COLUMNS.get(dimension)
    if not col:
        raise ValueError(f"未知维度: {dimension}")
    return upsert_dimension_score(stock_id, col, value)


def sync_factor_fundamental(stock_id: int, composite: float, calc_date: str | None = None) -> str:
    return upsert_dimension_score(stock_id, "fundamental_score", composite, calc_date=calc_date)


def _fallback_from_table(conn: sqlite3.Connection, stock_id: int, column: str) -> float | None:
    if column == "sentiment_score":
        row = conn.execute(
            """SELECT sentiment_score AS v FROM stock_news
               WHERE stock_id=? AND sentiment_score IS NOT NULL
               ORDER BY pub_date DESC LIMIT 1""",
            (stock_id,),
        ).fetchone()
        if row and row["v"] is not None:
            return float(row["v"])
        return None

    spec = DIMENSION_TABLES.get(column)
    if not spec:
        return None
    table, score_col, order_col = spec
    row = conn.execute(
        f"SELECT {score_col} AS v FROM {table} WHERE stock_id=? ORDER BY {order_col} DESC LIMIT 1",
        (stock_id,),
    ).fetchone()
    if row and row["v"] is not None:
        return float(row["v"])
    return None


def load_display_scores(stock_id: int, *, backfill: bool = True) -> dict[str, Any]:
    conn = _connect()
    result: dict[str, Any] = {"stock_id": stock_id, "previous": {}}
    calc_date: str | None = None
    try:
        current = conn.execute(
            "SELECT * FROM comprehensive_scores WHERE stock_id=? ORDER BY calc_date DESC LIMIT 1",
            (stock_id,),
        ).fetchone()
        previous = conn.execute(
            "SELECT * FROM comprehensive_scores WHERE stock_id=? ORDER BY calc_date DESC LIMIT 1 OFFSET 1",
            (stock_id,),
        ).fetchone()

        dim_cols = (
            "capital_score",
            "policy_score",
            "mood_score",
            "technical_score",
            "fundamental_score",
            "sentiment_score",
            "val_score",
            "composite_score",
        )
        calc_date = current["calc_date"] if current else None
        values: dict[str, float | None] = {}

        for col in dim_cols:
            val = current[col] if current else None
            if val is None:
                fb = _fallback_from_table(conn, stock_id, col)
                if fb is not None:
                    val = fb

            values[col] = float(val) if val is not None else None

        prev_map: dict[str, float | None] = {}
        for col in ("capital_score", "policy_score", "mood_score", "technical_score"):
            key = col.replace("_score", "")
            prev_map[key] = (
                float(previous[col]) if previous and previous[col] is not None else None
            )

        result = {
            "stock_id": stock_id,
            "calc_date": calc_date,
            "composite_score": values.get("composite_score"),
            "fundamental_score": values.get("fundamental_score"),
            "technical_score": values.get("technical_score"),
            "sentiment_score": values.get("sentiment_score"),
            "capital_score": values.get("capital_score"),
            "policy_score": values.get("policy_score"),
            "mood_score": values.get("mood_score"),
            "val_score": values.get("val_score"),
            "previous": prev_map,
        }
    finally:
        conn.close()

    return result
