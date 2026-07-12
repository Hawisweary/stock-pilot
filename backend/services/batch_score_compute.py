"""各维度 compute 步骤 — batch-fill P1～P4"""
from __future__ import annotations

import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Any, Callable

import config
from services.comprehensive_store import upsert_dimension_scores_batch
from services.sentiment_aggregate import batch_get_sentiment_scores


def _active_ids(stock_ids: list[int] | None) -> list[int]:
    if stock_ids:
        return stock_ids
    conn = sqlite3.connect(config.DB_PATH)
    try:
        rows = conn.execute("SELECT id FROM stocks WHERE is_active=1 ORDER BY id").fetchall()
        return [int(r[0]) for r in rows]
    finally:
        conn.close()


def _quotes_df_from_db(stock_id: int, *, min_rows: int = 20):
    """从 stock_daily_quotes 构建技术指标所需 OHLCV。"""
    import pandas as pd

    conn = sqlite3.connect(config.DB_PATH)
    try:
        rows = conn.execute(
            """SELECT trade_date, open, high, low, close, volume
               FROM stock_daily_quotes
               WHERE stock_id=? AND close IS NOT NULL
               ORDER BY trade_date ASC""",
            (stock_id,),
        ).fetchall()
    finally:
        conn.close()
    if len(rows) < min_rows:
        return None
    df = pd.DataFrame(
        rows, columns=["trade_date", "open", "high", "low", "close", "volume"]
    )
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"])
    if len(df) < min_rows:
        return None
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df.set_index("trade_date")


def _resample_weekly(df_daily):
    """日 K 聚合为周 K（用于周线技术指标）。"""
    if df_daily is None or df_daily.empty:
        return None
    w = df_daily.resample("W").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    ).dropna(subset=["close"])
    return w if not w.empty else None


def _fetch_price_df(code: str, market: str, *, frequency: str, count: int):
    if market == "US":
        import yfinance as yf

        from services.data_processor import to_yfinance_code

        yf_code = to_yfinance_code(code, market="US")
        interval = "1d" if frequency == "1d" else "1wk"
        hist = yf.Ticker(yf_code).history(period="2y", interval=interval, auto_adjust=True)
        if hist is None or hist.empty:
            return None
        hist = hist.tail(count).rename(
            columns={
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            }
        )
        return hist[["open", "high", "low", "close", "volume"]]
    ash_code = f"sh{code}" if str(code).startswith(("6", "9")) else f"sz{code}"
    return _ashare_get_price(ash_code, frequency=frequency, count=count)


def _ashare_get_price(code: str, *, frequency: str, count: int):
    """Ashare 拉价带超时，避免 batch technical 阶段无限挂起。"""
    from services.Ashare import get_price

    timeout = config.ASHARE_FETCH_TIMEOUT_SEC

    def _fetch():
        return get_price(code, frequency=frequency, count=count)

    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(_fetch)
        try:
            return fut.result(timeout=timeout)
        except FuturesTimeout:
            raise TimeoutError(f"get_price timeout after {timeout}s: {code} {frequency}")


def _load_tech_cache_scores(stock_ids: list[int]) -> dict[int, float]:
    if not stock_ids:
        return {}
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        ph = ",".join(["?"] * len(stock_ids))
        rows = conn.execute(
            f"""
            SELECT tc.stock_id, tc.score
            FROM tech_analysis_cache tc
            INNER JOIN (
                SELECT stock_id, MAX(created_at) AS md FROM tech_analysis_cache
                WHERE stock_id IN ({ph}) GROUP BY stock_id
            ) t ON tc.stock_id = t.stock_id AND tc.created_at = t.md
            WHERE tc.score IS NOT NULL
            """,
            stock_ids,
        ).fetchall()
        return {int(r["stock_id"]): float(r["score"]) for r in rows}
    finally:
        conn.close()


def _persist_policy_rows(rows: list[tuple[int, float, str]], calc_date: str) -> None:
    if not rows:
        return
    from database import get, write_lock

    with write_lock:
        db = get()
        for stock_id, score, breakdown_json in rows:
            db.execute(
                """INSERT OR REPLACE INTO policy_scores
                (stock_id, date, composite_score, breakdown_json)
                VALUES (?, ?, ?, ?)""",
                (stock_id, calc_date, score, breakdown_json),
            )
        db.commit()


def compute_fundamental(stock_ids: list[int] | None, calc_date: str) -> dict[str, Any]:
    from database import get
    from services.factor_engine import FactorEngine
    from services.factor_percentile_cache import get_universe_metrics

    ids = _active_ids(stock_ids)
    t0 = time.perf_counter()
    db = get()
    get_universe_metrics(calc_date, db)
    fe = FactorEngine(db)
    all_active = len(fe._active_stock_ids())
    if ids and len(ids) < all_active:
        results = fe.calculate_incremental(ids, sync_comprehensive=False)
    else:
        results = fe.calculate_all(ids, sync_comprehensive=False)
    ok = [r for r in results if "error" not in r]
    batch: dict[int, dict[str, float]] = {}
    for r in ok:
        batch[int(r["stock_id"])] = {"fundamental_score": float(r["composite_score"])}
    synced = upsert_dimension_scores_batch(batch, calc_date) if batch else 0
    return {
        "dimension": "fundamental_score",
        "computed": len(ok),
        "synced": synced,
        "errors": [r for r in results if "error" in r],
        "duration_ms": int((time.perf_counter() - t0) * 1000),
    }


def compute_capital(stock_ids: list[int] | None, calc_date: str) -> dict[str, Any]:
    from services.capital_scorer import compute_all_capital
    from services.comprehensive_store import upsert_dimension_scores_to_latest_rows

    id_set = set(stock_ids) if stock_ids else None
    t0 = time.perf_counter()
    rows = compute_all_capital(calc_date)
    batch: dict[int, dict[str, float]] = {}
    for r in rows:
        sid = int(r["stock_id"])
        if id_set and sid not in id_set:
            continue
        batch[sid] = {
            "capital_score": float(r.get("composite_score", r.get("score", 0))),
        }
    synced = upsert_dimension_scores_to_latest_rows(batch) if batch else 0
    return {
        "dimension": "capital_score",
        "computed": len(rows),
        "synced": synced if isinstance(synced, int) else len(synced),
        "duration_ms": int((time.perf_counter() - t0) * 1000),
    }


def compute_mood(stock_ids: list[int] | None, calc_date: str) -> dict[str, Any]:
    from services.sentiment_scorer import compute_all_sentiment
    from services.comprehensive_store import upsert_dimension_scores_to_latest_rows
    from database import write_lock

    id_set = set(stock_ids) if stock_ids else None
    t0 = time.perf_counter()
    rows = compute_all_sentiment(calc_date)
    batch: dict[int, dict[str, float]] = {}
    sentiment_rows = []
    for r in rows:
        sid = int(r["stock_id"])
        if id_set and sid not in id_set:
            continue
        score = float(r.get("score", r.get("composite_score", 0)))
        batch[sid] = {"mood_score": score}
        sentiment_rows.append((sid, calc_date, score))

    # 同步写 sentiment_scores（V5 scorer 直接读此表）
    if sentiment_rows:
        with write_lock:
            conn = sqlite3.connect(config.DB_PATH, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executemany(
                "INSERT OR REPLACE INTO sentiment_scores (stock_id, date, composite_score) VALUES (?,?,?)",
                sentiment_rows,
            )
            conn.commit()
            conn.close()

    synced = upsert_dimension_scores_to_latest_rows(batch) if batch else 0
    return {
        "dimension": "mood_score",
        "computed": len(rows),
        "synced": synced if isinstance(synced, int) else len(synced),
        "sentiment_scores_written": len(sentiment_rows),
        "duration_ms": int((time.perf_counter() - t0) * 1000),
    }


def compute_policy(stock_ids: list[int] | None, calc_date: str) -> dict[str, Any]:
    from services.policy_scorer import compute_policy_score

    ids = stock_ids or _active_ids(None)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    stocks = conn.execute(
        f"SELECT id, code FROM stocks WHERE id IN ({','.join(['?'] * len(ids))})",
        ids,
    ).fetchall()
    conn.close()

    use_llm = config.BATCH_POLICY_USE_LLM
    t0 = time.perf_counter()
    policy_rows: list[tuple[int, float, str]] = []
    batch: dict[int, dict[str, float]] = {}
    errors: list[dict] = []
    for s in stocks:
        try:
            r = compute_policy_score(int(s["id"]), s["code"], use_llm=use_llm)
            score = float(r.get("composite_score", 50))
            sid = int(s["id"])
            policy_rows.append(
                (sid, score, json.dumps(r.get("keywords", []), ensure_ascii=False)),
            )
            batch[sid] = {"policy_score": score}
        except Exception as e:
            errors.append({"stock_id": int(s["id"]), "reason": str(e)})
    _persist_policy_rows(policy_rows, calc_date)
    from services.comprehensive_store import upsert_dimension_scores_to_latest_rows

    synced = upsert_dimension_scores_to_latest_rows(batch) if batch else 0
    return {
        "dimension": "policy_score",
        "computed": synced,
        "synced": synced,
        "use_llm": use_llm,
        "errors": errors,
        "duration_ms": int((time.perf_counter() - t0) * 1000),
    }


def compute_valuation(stock_ids: list[int] | None, calc_date: str) -> dict[str, Any]:
    from services.comprehensive_store import upsert_dimension_scores_to_latest_rows
    from services.valuation_engine import compute_valuation_scores
    from services.valuation_prefetch import prefetch_valuation_snapshots

    id_set = set(stock_ids) if stock_ids else None
    t0 = time.perf_counter()
    prefetch = prefetch_valuation_snapshots(stock_ids)
    r = compute_valuation_scores(sync_comprehensive=False, score_date=calc_date)
    conn = sqlite3.connect(config.DB_PATH)
    batch: dict[int, dict[str, float]] = {}
    try:
        rows = conn.execute(
            """SELECT vs.stock_id, vs.composite_score
               FROM valuation_scores vs
               INNER JOIN (
                   SELECT stock_id, MAX(date) AS md FROM valuation_scores
                   GROUP BY stock_id
               ) t ON vs.stock_id = t.stock_id AND vs.date = t.md
               WHERE vs.composite_score IS NOT NULL""",
        ).fetchall()
        for stock_id, score in rows:
            sid = int(stock_id)
            if id_set and sid not in id_set:
                continue
            batch[sid] = {"val_score": float(score)}
    finally:
        conn.close()
    synced = upsert_dimension_scores_to_latest_rows(batch) if batch else 0
    return {
        "dimension": "val_score",
        "computed": r.get("computed", 0),
        "synced": synced,
        "prefetch": prefetch,
        "duration_ms": int((time.perf_counter() - t0) * 1000),
    }


def compute_sentiment_news(
    stock_ids: list[int] | None,
    calc_date: str,
    *,
    prefetch: bool = False,
) -> dict[str, Any]:
    if prefetch and stock_ids:
        from services.score_gap_fetch import fetch_sentiment_for_gaps

        fetch_sentiment_for_gaps(stock_ids=stock_ids, include_stale=True)

    conn = sqlite3.connect(config.DB_PATH)
    ids = stock_ids or _active_ids(None)
    t0 = time.perf_counter()
    from services.sentiment_aggregate import resolve_sentiment_scores

    sentiment_map = resolve_sentiment_scores(conn, ids, calc_date)
    conn.close()
    batch: dict[int, dict[str, float]] = {}
    skipped = 0
    for sid in ids:
        score = sentiment_map.get(sid)
        if score is None:
            skipped += 1
            continue
        batch[sid] = {"sentiment_score": score}
    synced = upsert_dimension_scores_batch(batch, calc_date) if batch else 0
    return {
        "dimension": "sentiment_score",
        "synced": synced,
        "skipped_null": skipped,
        "duration_ms": int((time.perf_counter() - t0) * 1000),
    }


def compute_technical(
    stock_ids: list[int],
    calc_date: str,
    *,
    heartbeat: Callable[[], None] | None = None,
) -> dict[str, Any]:
    from services.technical_rule_engine import (
        compute_technical_tier,
        ohlcv_hash,
        persist_rule_result,
    )

    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    placeholders = ",".join(["?"] * len(stock_ids))
    stocks = conn.execute(
        f"SELECT id, code, name, COALESCE(market, 'A') AS market FROM stocks WHERE id IN ({placeholders})",
        stock_ids,
    ).fetchall()
    conn.close()

    t0 = time.perf_counter()
    batch: dict[int, dict[str, float]] = {}
    from_cache = 0
    failed: list[dict] = []
    still_no_source: list[dict] = []

    for i, s in enumerate(stocks):
        if heartbeat and i % 3 == 0:
            heartbeat()
        sid = int(s["id"])

        try:
            market = str(s["market"] or "A")
            code = str(s["code"])
            df_d = _quotes_df_from_db(sid)
            if df_d is None:
                df_d = _fetch_price_df(code, market, frequency="1d", count=120)
            if df_d is None or df_d.empty or len(df_d) < 20:
                still_no_source.append(
                    {"stock_id": sid, "reason": "quotes<20", "market": market}
                )
                continue

            ih = ohlcv_hash(df_d)
            result = compute_technical_tier(df_d)
            score = result.get("score")
            if score is None:
                still_no_source.append({"stock_id": sid, "reason": "rule_returned_null"})
                continue
            persist_rule_result(sid, result, input_hash=ih)
            batch[sid] = {"technical_score": float(score)}
        except TimeoutError as e:
            failed.append({"stock_id": sid, "reason": str(e)[:200]})
        except Exception as e:
            failed.append({"stock_id": sid, "reason": str(e)[:200]})

    from services.comprehensive_store import upsert_dimension_scores_to_latest_rows

    filled = upsert_dimension_scores_to_latest_rows(batch) if batch else 0
    return {
        "dimension": "technical_score",
        "filled": filled,
        "from_cache": from_cache,
        "engine": "technical_rule_v1",
        "failed_stocks": failed,
        "still_no_source": still_no_source,
        "duration_ms": int((time.perf_counter() - t0) * 1000),
    }


COMPUTE_HANDLERS: dict[str, Callable[..., dict]] = {
    "fundamental_score": compute_fundamental,
    "capital_score": compute_capital,
    "mood_score": compute_mood,
    "policy_score": compute_policy,
    "val_score": compute_valuation,
    "sentiment_score": compute_sentiment_news,
}
