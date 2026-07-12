"""no_source 分维度轻量 prefetch 探测（dry-run / 计划用，不写 DB）"""
from __future__ import annotations

import sqlite3
from typing import Any

import config
from services.batch_score_plan import probe_technical_quotes

_PREFETCH_EST_MS: dict[str, tuple[int, int]] = {
    "fundamental_score": (5000, 60000),
    "technical_score": (8000, 25000),
    "sentiment_score": (3000, 15000),
    "capital_score": (2000, 8000),
    "mood_score": (2000, 8000),
    "val_score": (1000, 5000),
    "policy_score": (0, 0),
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _probe_fundamental(conn: sqlite3.Connection, stock_ids: list[int]) -> list[dict]:
    if not stock_ids:
        return []
    ph = ",".join(["?"] * len(stock_ids))
    try:
        rows = conn.execute(
            f"""
            SELECT stock_id, COUNT(DISTINCT report_date) AS c
            FROM financial_reports
            WHERE stock_id IN ({ph})
            GROUP BY stock_id
            """,
            tuple(stock_ids),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    ok = {int(r["stock_id"]): int(r["c"]) for r in rows}
    out: list[dict] = []
    for sid in stock_ids:
        if ok.get(sid, 0) < 2:
            lo, hi = _PREFETCH_EST_MS["fundamental_score"]
            out.append(
                {
                    "stock_id": sid,
                    "dimension": "fundamental_score",
                    "would_fetch": True,
                    "reason": "financial_reports<2",
                    "estimated_extra_ms": [lo, hi],
                }
            )
    return out


def _probe_sentiment(conn: sqlite3.Connection, stock_ids: list[int]) -> list[dict]:
    if not stock_ids:
        return []
    ph = ",".join(["?"] * len(stock_ids))
    try:
        rows = conn.execute(
            f"""
            SELECT stock_id, COUNT(*) AS c FROM stock_news
            WHERE stock_id IN ({ph})
              AND pub_date >= date('now', ?)
            GROUP BY stock_id
            """,
            (*stock_ids, f"-{config.SENTIMENT_WINDOW_DAYS} days"),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    ok = {int(r["stock_id"]): int(r["c"]) for r in rows}
    out: list[dict] = []
    for sid in stock_ids:
        if ok.get(sid, 0) <= 0:
            lo, hi = _PREFETCH_EST_MS["sentiment_score"]
            out.append(
                {
                    "stock_id": sid,
                    "dimension": "sentiment_score",
                    "would_fetch": True,
                    "reason": "news<7d",
                    "estimated_extra_ms": [lo, hi],
                }
            )
    return out


def _probe_quotes_latest(conn: sqlite3.Connection, stock_ids: list[int], dimension: str) -> list[dict]:
    if not stock_ids:
        return []
    ph = ",".join(["?"] * len(stock_ids))
    try:
        rows = conn.execute(
            f"""
            SELECT stock_id, MAX(trade_date) AS md FROM stock_daily_quotes
            WHERE stock_id IN ({ph}) GROUP BY stock_id
            """,
            tuple(stock_ids),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    latest = {int(r["stock_id"]): r["md"] for r in rows}
    target = config.latest_trading_date()
    out: list[dict] = []
    lo, hi = _PREFETCH_EST_MS.get(dimension, (2000, 8000))
    for sid in stock_ids:
        md = latest.get(sid)
        if not md or str(md) < target:
            out.append(
                {
                    "stock_id": sid,
                    "dimension": dimension,
                    "would_fetch": True,
                    "reason": "quotes_stale_or_missing",
                    "estimated_extra_ms": [lo, hi],
                }
            )
    return out


def _probe_valuation(conn: sqlite3.Connection, stock_ids: list[int]) -> list[dict]:
    if not stock_ids:
        return []
    ph = ",".join(["?"] * len(stock_ids))
    try:
        rows = conn.execute(
            f"""
            SELECT DISTINCT stock_id FROM valuation_snapshots WHERE stock_id IN ({ph})
            """,
            tuple(stock_ids),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    ok = {int(r["stock_id"]) for r in rows}
    out: list[dict] = []
    lo, hi = _PREFETCH_EST_MS["val_score"]
    for sid in stock_ids:
        if sid not in ok:
            out.append(
                {
                    "stock_id": sid,
                    "dimension": "val_score",
                    "would_fetch": True,
                    "reason": "no_valuation_snapshot",
                    "estimated_extra_ms": [lo, hi],
                }
            )
    return out


def prefetch_if_needed(
    dimension: str,
    stock_ids: list[int],
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    """返回 attempted / would_fetch / details（仅探测，dry_run 时不写入）。"""
    if not stock_ids:
        return {
            "dimension": dimension,
            "attempted": 0,
            "would_fetch": 0,
            "would_succeed": 0,
            "still_no_source": 0,
            "dry_run": dry_run,
            "details": [],
        }

    details: list[dict] = []
    if dimension == "technical_score":
        probe = probe_technical_quotes(stock_ids)
        details = probe.get("details", [])
        for d in details:
            lo, hi = _PREFETCH_EST_MS["technical_score"]
            d["estimated_extra_ms"] = [lo, hi]
        would_fetch = probe.get("would_fetch", 0)
    elif dimension == "fundamental_score":
        conn = _connect()
        try:
            details = _probe_fundamental(conn, stock_ids)
        finally:
            conn.close()
        would_fetch = len(details)
    elif dimension == "sentiment_score":
        conn = _connect()
        try:
            details = _probe_sentiment(conn, stock_ids)
        finally:
            conn.close()
        would_fetch = len(details)
    elif dimension in ("capital_score", "mood_score"):
        conn = _connect()
        try:
            details = _probe_quotes_latest(conn, stock_ids, dimension)
        finally:
            conn.close()
        would_fetch = len(details)
    elif dimension == "val_score":
        conn = _connect()
        try:
            details = _probe_valuation(conn, stock_ids)
        finally:
            conn.close()
        would_fetch = len(details)
    else:
        would_fetch = 0

    attempted = len(stock_ids)
    return {
        "dimension": dimension,
        "attempted": attempted,
        "would_fetch": would_fetch,
        "would_succeed": max(0, attempted - would_fetch),
        "still_no_source": would_fetch,
        "dry_run": dry_run,
        "details": details,
    }


def _no_source_targets(gap_report: dict) -> dict[str, list[int]]:
    by_dim: dict[str, set[int]] = {}
    for g in gap_report.get("gaps", []):
        if g.get("status") != "no_source":
            continue
        dim = g.get("dimension")
        if dim:
            by_dim.setdefault(dim, set()).add(int(g["stock_id"]))
    return {k: sorted(v) for k, v in by_dim.items()}


def execute_prefetch(dimension: str, stock_ids: list[int], *, max_batch: int = 30) -> dict[str, Any]:
    """对 no_source 股票实际拉取源数据（写 DB）。"""
    probe = prefetch_if_needed(dimension, stock_ids, dry_run=True)
    target_ids = [int(d["stock_id"]) for d in probe.get("details", [])][:max_batch]
    if not target_ids:
        return {**probe, "dry_run": False, "fetched": 0, "skipped": True}

    fetched = 0
    errors: list[dict] = []

    if dimension == "sentiment_score":
        from services.score_gap_fetch import fetch_sentiment_for_gaps

        r = fetch_sentiment_for_gaps(stock_ids=target_ids, include_stale=True)
        return {**probe, "dry_run": False, "fetched": r.get("attempted", len(target_ids)), "result": r}

    if dimension == "technical_score":
        from services.batch_score_compute import _ashare_get_price
        from database import get

        conn = get()
        for sid in target_ids:
            try:
                row = conn.execute("SELECT code FROM stocks WHERE id=?", (sid,)).fetchone()
                if not row:
                    continue
                code = str(row[0])
                ash = f"sh{code}" if code.startswith(("6", "9")) else f"sz{code}"
                df = _ashare_get_price(ash, frequency="1d", count=120)
                if df is not None and not df.empty:
                    fetched += 1
            except Exception as e:
                errors.append({"stock_id": sid, "error": str(e)[:200]})
        return {**probe, "dry_run": False, "fetched": fetched, "errors": errors}

    if dimension == "fundamental_score":
        from database import get
        from services.data_fetcher import DataFetcher

        conn = get()
        fetcher = DataFetcher(conn)
        for sid in target_ids:
            try:
                row = conn.execute("SELECT code, market FROM stocks WHERE id=?", (sid,)).fetchone()
                if not row:
                    continue
                market = row[1] if len(row) > 1 and row[1] else "A"
                r = fetcher.fetch_all_for_stock(sid, row[0], market)
                if r.get("financials_count", 0) or r.get("indicators_count", 0):
                    fetched += 1
            except Exception as e:
                errors.append({"stock_id": sid, "error": str(e)[:200]})
        return {**probe, "dry_run": False, "fetched": fetched, "errors": errors}

    if dimension == "val_score":
        from services.valuation_engine import compute_valuation_scores
        from services.valuation_prefetch import prefetch_valuation_snapshots

        prefetch = prefetch_valuation_snapshots(target_ids)
        r = compute_valuation_scores()
        return {**probe, "dry_run": False, "fetched": r.get("computed", 0), "prefetch": prefetch, "result": r}

    return {**probe, "dry_run": False, "fetched": 0, "skipped": True}


def execute_prefetch_for_gaps(gap_report: dict, *, max_batch: int = 30) -> dict[str, dict]:
    targets = _no_source_targets(gap_report)
    out: dict[str, dict] = {}
    for dim, ids in targets.items():
        if ids:
            out[dim] = execute_prefetch(dim, ids, max_batch=max_batch)
    return out


def prefetch_for_gaps(gap_report: dict) -> dict[str, dict]:
    """按 gap 报告批量 prefetch（no_source / missing 股票）。"""
    by_dim: dict[str, set[int]] = {}
    for g in gap_report.get("gaps", []):
        if g.get("status") not in ("no_source", "missing"):
            continue
        dim = g.get("dimension")
        if not dim:
            continue
        by_dim.setdefault(dim, set()).add(int(g["stock_id"]))

    out: dict[str, dict] = {}
    for dim, ids in by_dim.items():
        out[dim] = prefetch_if_needed(dim, sorted(ids))
    return out
