"""模拟交易组合管理 — lot 级 T+1、成本、按评分建仓"""
from __future__ import annotations

import sqlite3
from db_util import connect_db
from datetime import date
from typing import List, Optional

from config import DB_PATH, DUAL_MA_MIN_HOLD_DAYS
from services.trade_pricing import (
    get_effective_trade_date,
    get_market_context,
    pricing_context_dict,
    resolve_mark_prices,
    resolve_mark_prices_with_meta,
    resolve_trade_price,
)
from services.trading_rules import (
    COMMISSION,
    LOT_SIZE,
    cost_basis_price,
    normalize_shares,
    split_cost,
    validate_shares,
)
from services.strategy_registry import get_meta, is_valid_strategy, normalize_strategy_id
from services.strategy_selector import (
    momentum_crash_reason,
    select_sector_rebalance,
    select_top_n_dicts,
)
from services.pnl_metrics import (
    aggregate_totals,
    build_position_pnl,
    dedupe_positions,
    estimated_buy_friction_pct,
)


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS portfolios (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, initial_cash REAL, cash REAL, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS portfolio_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, portfolio_id INTEGER, stock_id INTEGER,
            shares INTEGER, avg_cost REAL, buy_date TEXT,
            UNIQUE(portfolio_id, stock_id)
        );
        CREATE TABLE IF NOT EXISTS portfolio_lots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            portfolio_id INTEGER NOT NULL, stock_id INTEGER NOT NULL,
            shares INTEGER NOT NULL, avg_cost REAL NOT NULL, buy_date TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS trade_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT, portfolio_id INTEGER, stock_id INTEGER,
            action TEXT, shares INTEGER, price REAL, trade_date TEXT, code TEXT, name TEXT,
            commission REAL, tax REAL, cash_delta REAL
        );
        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            portfolio_id INTEGER, snapshot_date TEXT, total_value REAL,
            PRIMARY KEY (portfolio_id, snapshot_date)
        );
    """)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(portfolio_positions)").fetchall()]
    if "buy_date" not in cols:
        conn.execute("ALTER TABLE portfolio_positions ADD COLUMN buy_date TEXT")
    if "turtle_stop_price" not in cols:
        conn.execute("ALTER TABLE portfolio_positions ADD COLUMN turtle_stop_price REAL")

    tj_cols = [r[1] for r in conn.execute("PRAGMA table_info(trade_journal)").fetchall()]
    for col, typ in [("stock_id", "INTEGER"), ("trade_date", "TEXT"), ("name", "TEXT"),
                     ("commission", "REAL"), ("tax", "REAL"), ("cash_delta", "REAL"),
                     ("reason", "TEXT"), ("strategy", "TEXT"), ("notes", "TEXT"),
                     ("price_source", "TEXT"), ("quote_date", "TEXT"), ("raw_price", "REAL")]:
        if col not in tj_cols:
            conn.execute(f"ALTER TABLE trade_journal ADD COLUMN {col} {typ}")

    _migrate_positions_to_lots(conn)
    _ensure_portfolio_settings_cols(conn)
    _ensure_pending_orders_table(conn)


def _ensure_pending_orders_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_pending_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            portfolio_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            signal_date TEXT NOT NULL,
            execute_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            code TEXT,
            shares INTEGER,
            reason TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            executed_at TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_portfolio_pending_exec
        ON portfolio_pending_orders(status, execute_date)
    """)


def _exec_date_after_signal(signal: date | None = None) -> str:
    from services.trade_calendar import next_trading_day

    base = signal or date.today()
    return next_trading_day(base).isoformat()


def _ensure_portfolio_settings_cols(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(portfolios)").fetchall()}
    for col, ddl in [
        ("owner_id", "owner_id TEXT DEFAULT 'default'"),
        ("rebalance_schedule", "rebalance_schedule TEXT DEFAULT 'none'"),
        ("last_rebalance_date", "last_rebalance_date TEXT"),
        ("max_weight_pct", "max_weight_pct REAL DEFAULT 30"),
        ("min_cash_pct", "min_cash_pct REAL DEFAULT 5"),
        ("default_strategy", "default_strategy TEXT DEFAULT 'composite'"),
        ("default_top_n", "default_top_n INTEGER DEFAULT 5"),
        ("default_min_score", "default_min_score REAL DEFAULT 50"),
        ("default_pos_style", "default_pos_style TEXT DEFAULT 'equal'"),
        ("default_combination_id", "default_combination_id INTEGER"),
        ("default_lookback", "default_lookback INTEGER DEFAULT 20"),
        ("default_sector_window", "default_sector_window INTEGER DEFAULT 5"),
        ("default_per_sector", "default_per_sector INTEGER DEFAULT 2"),
        ("skip_next_rebalance", "skip_next_rebalance INTEGER DEFAULT 0"),
    ]:
        if col not in cols:
            conn.execute(f"ALTER TABLE portfolios ADD COLUMN {ddl}")


def _portfolio_settings(row: sqlite3.Row | dict) -> dict:
    d = dict(row) if not isinstance(row, dict) else row
    return {
        "owner_id": d.get("owner_id") or "default",
        "rebalance_schedule": d.get("rebalance_schedule") or "none",
        "last_rebalance_date": d.get("last_rebalance_date"),
        "max_weight_pct": float(d.get("max_weight_pct") or 30),
        "min_cash_pct": float(d.get("min_cash_pct") or 5),
        "default_strategy": d.get("default_strategy") or "composite",
        "default_top_n": int(d.get("default_top_n") or 5),
        "default_min_score": float(d.get("default_min_score") or 50),
        "default_pos_style": d.get("default_pos_style") or "equal",
        "default_combination_id": d.get("default_combination_id"),
        "default_lookback": int(d.get("default_lookback") or 20),
        "default_sector_window": int(d.get("default_sector_window") or 5),
        "default_per_sector": int(d.get("default_per_sector") or 2),
    }


def _check_buy_risk(
    conn: sqlite3.Connection,
    portfolio_id: int,
    stock_id: int,
    add_shares: int,
    price: float,
    cash_after: float,
    settings: dict,
) -> str | None:
    tv = calc_total_value(portfolio_id, _conn=conn)
    if tv <= 0:
        return None
    min_cash = settings.get("min_cash_pct", 5)
    if cash_after < tv * min_cash / 100:
        return f"风控：现金须保留至少 {min_cash}%"
    max_w = settings.get("max_weight_pct", 30)
    cur = conn.execute(
        "SELECT COALESCE(shares,0) FROM portfolio_positions WHERE portfolio_id=? AND stock_id=?",
        (portfolio_id, stock_id),
    ).fetchone()
    total_sh = int(cur[0] if cur else 0) + add_shares
    new_tv = cash_after + sum(
        s * (p or 0)
        for s, p in conn.execute(
            """SELECT pp.shares, q.close FROM portfolio_positions pp
               LEFT JOIN (SELECT stock_id, close FROM stock_daily_quotes
                          WHERE (stock_id, trade_date) IN
                          (SELECT stock_id, MAX(trade_date) FROM stock_daily_quotes GROUP BY stock_id)) q
               ON pp.stock_id=q.stock_id WHERE pp.portfolio_id=? AND pp.stock_id!=?""",
            (portfolio_id, stock_id),
        ).fetchall()
    ) + total_sh * price
    if new_tv > 0 and total_sh * price / new_tv * 100 > max_w + 0.01:
        return f"风控：单票权重不得超过 {max_w}%"
    return None


def _migrate_positions_to_lots(conn: sqlite3.Connection) -> None:
    """旧持仓 → lot（一次性）"""
    rows = conn.execute(
        """SELECT pp.portfolio_id, pp.stock_id, pp.shares, pp.avg_cost, pp.buy_date
           FROM portfolio_positions pp
           WHERE pp.shares > 0
             AND NOT EXISTS (
               SELECT 1 FROM portfolio_lots pl
               WHERE pl.portfolio_id = pp.portfolio_id AND pl.stock_id = pp.stock_id
             )"""
    ).fetchall()
    today = date.today().strftime("%Y-%m-%d")
    for r in rows:
        conn.execute(
            """INSERT INTO portfolio_lots (portfolio_id, stock_id, shares, avg_cost, buy_date)
               VALUES (?,?,?,?,?)""",
            (r[0], r[1], r[2], r[3], r[4] or today),
        )


def _sync_positions_from_lots(conn: sqlite3.Connection, portfolio_id: int) -> None:
    stop_map = {
        int(r[0]): r[1]
        for r in conn.execute(
            "SELECT stock_id, turtle_stop_price FROM portfolio_positions WHERE portfolio_id=?",
            (portfolio_id,),
        ).fetchall()
        if r[1] is not None
    }
    lots = conn.execute(
        """SELECT stock_id, SUM(shares) AS shares,
                  SUM(shares * avg_cost) / NULLIF(SUM(shares), 0) AS avg_cost,
                  MIN(buy_date) AS buy_date
           FROM portfolio_lots WHERE portfolio_id=? AND shares > 0
           GROUP BY stock_id""",
        (portfolio_id,),
    ).fetchall()
    conn.execute("DELETE FROM portfolio_positions WHERE portfolio_id=?", (portfolio_id,))
    for lot in lots:
        if lot[1] and lot[1] > 0:
            conn.execute(
                """INSERT INTO portfolio_positions
                   (portfolio_id, stock_id, shares, avg_cost, buy_date, turtle_stop_price)
                   VALUES (?,?,?,?,?,?)""",
                (
                    portfolio_id,
                    lot[0],
                    int(lot[1]),
                    round(float(lot[2] or 0), 3),
                    lot[3],
                    stop_map.get(int(lot[0])),
                ),
            )


def assert_lots_positions_sync(portfolio_id: int, conn: Optional[sqlite3.Connection] = None) -> None:
    """开发期断言：portfolio_lots 汇总必须与 portfolio_positions 一致。

    在测试或 debug 模式下调用，发现漂移时抛 AssertionError。
    """
    external = conn is not None
    c = conn or connect_db()
    lots = {
        int(r[0]): int(r[1])
        for r in c.execute(
            "SELECT stock_id, SUM(shares) FROM portfolio_lots WHERE portfolio_id=? AND shares>0 GROUP BY stock_id",
            (portfolio_id,),
        ).fetchall()
    }
    positions = {
        int(r[0]): int(r[1])
        for r in c.execute(
            "SELECT stock_id, shares FROM portfolio_positions WHERE portfolio_id=? AND shares>0",
            (portfolio_id,),
        ).fetchall()
    }
    if not external:
        c.close()
    if lots != positions:
        raise AssertionError(
            f"lots/positions 漂移 portfolio_id={portfolio_id}: lots={lots} positions={positions}"
        )


def _quote_series_for_turtle(
    conn: sqlite3.Connection,
    stock_id: int,
    bars: int,
) -> tuple[dict, list[str]]:
    rows = conn.execute(
        """SELECT trade_date, close, high, low FROM stock_daily_quotes
           WHERE stock_id=? AND close IS NOT NULL
           ORDER BY trade_date DESC LIMIT ?""",
        (stock_id, bars),
    ).fetchall()
    if not rows:
        return {}, []
    rows = list(reversed(rows))
    dates = [str(r[0]) for r in rows]
    series = {
        str(r[0]): {
            "close": float(r[1]),
            "high": float(r[2] or r[1]),
            "low": float(r[3] or r[1]),
        }
        for r in rows
    }
    return series, dates


def _set_turtle_stop(
    conn: sqlite3.Connection,
    portfolio_id: int,
    stock_id: int,
    entry_price: float,
    lookback: int,
) -> float | None:
    from services.strategies.turtle import turtle_atr

    series, dates = _quote_series_for_turtle(conn, stock_id, lookback + 5)
    if not dates:
        return None
    atr = turtle_atr(series, dates, len(dates) - 1, period=lookback)
    stop = round(entry_price - 2 * atr, 4) if atr and atr > 0 else None
    conn.execute(
        """UPDATE portfolio_positions SET turtle_stop_price=?
           WHERE portfolio_id=? AND stock_id=?""",
        (stop, portfolio_id, stock_id),
    )
    return stop


def _sellable_shares(
    conn: sqlite3.Connection,
    portfolio_id: int,
    stock_id: int,
    apply_t1: bool,
    as_of_date: Optional[str] = None,
) -> int:
    if apply_t1:
        today = as_of_date or get_effective_trade_date(get_market_context(conn))
        row = conn.execute(
            """SELECT COALESCE(SUM(shares), 0) FROM portfolio_lots
               WHERE portfolio_id=? AND stock_id=? AND shares > 0 AND buy_date < ?""",
            (portfolio_id, stock_id, today),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT COALESCE(SUM(shares), 0) FROM portfolio_lots
               WHERE portfolio_id=? AND stock_id=? AND shares > 0""",
            (portfolio_id, stock_id),
        ).fetchone()
    return int(row[0] if row else 0)


def _sell_from_lots(
    conn: sqlite3.Connection,
    portfolio_id: int,
    stock_id: int,
    shares: int,
    apply_t1: bool,
) -> None:
    today = get_effective_trade_date(get_market_context(conn))
    remaining = shares
    query = """SELECT id, shares FROM portfolio_lots
               WHERE portfolio_id=? AND stock_id=? AND shares > 0"""
    params: list = [portfolio_id, stock_id]
    if apply_t1:
        query += " AND buy_date < ?"
        params.append(today)
    query += " ORDER BY buy_date ASC, id ASC"
    lots = conn.execute(query, params).fetchall()
    for lot_id, lot_sh in lots:
        if remaining <= 0:
            break
        take = min(remaining, lot_sh)
        new_sh = lot_sh - take
        if new_sh <= 0:
            conn.execute("DELETE FROM portfolio_lots WHERE id=?", (lot_id,))
        else:
            conn.execute("UPDATE portfolio_lots SET shares=? WHERE id=?", (new_sh, lot_id))
        remaining -= take
    if remaining > 0:
        raise ValueError("持仓不足")


def create_portfolio(name: str, initial_cash: float = 100000, owner_id: str = "default") -> dict:
    conn = connect_db(write=True)
    conn.row_factory = sqlite3.Row
    _ensure_tables(conn)
    today = date.today().strftime("%Y-%m-%d")
    conn.execute(
        "INSERT INTO portfolios (name, initial_cash, cash, created_at, owner_id) VALUES (?,?,?,?,?)",
        (name, initial_cash, initial_cash, today, owner_id),
    )
    pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT OR REPLACE INTO portfolio_snapshots (portfolio_id, snapshot_date, total_value) VALUES (?,?,?)",
        (pid, today, initial_cash),
    )
    conn.commit()
    conn.close()
    return {"id": pid, "name": name, "cash": initial_cash, "total_value": initial_cash}


# 实时行情 TTL 缓存：腾讯接口偶发超时(5-8秒)会拖垮组合页每次加载,
# 盘中20秒/收盘后10分钟内直接复用,拉取失败时用上次结果兜底
_rt_cache_store: dict = {"data": {}, "ts": 0.0}


def _rt_cache_ttl() -> float:
    from datetime import datetime

    now = datetime.now()
    is_trading_hours = now.weekday() < 5 and 9 <= now.hour < 15
    return 20.0 if is_trading_hours else 600.0


def fetch_rt_cache_for_all_portfolios() -> dict[str, dict]:
    """跨所有组合去重股票代码，一次性批量拉实时行情(带TTL缓存+失败兜底)。

    供 get_portfolios() / get_pnl_summary() 等需要跨多个组合展示市值的场景复用，
    避免每个组合各自触发一次 tencent_quote 外部请求（N 个组合 = N 次网络往返）。
    """
    import time as _time

    if _rt_cache_store["data"] and _time.time() - _rt_cache_store["ts"] < _rt_cache_ttl():
        return _rt_cache_store["data"]

    conn = sqlite3.connect(DB_PATH)
    try:
        codes = [
            r[0]
            for r in conn.execute(
                """SELECT DISTINCT s.code FROM portfolio_positions pp
                   JOIN stocks s ON pp.stock_id = s.id"""
            ).fetchall()
        ]
    finally:
        conn.close()
    if not codes:
        return {}
    try:
        from services.data_sources import tencent_quote

        data = tencent_quote(codes)
        if data:
            _rt_cache_store["data"] = data
            _rt_cache_store["ts"] = _time.time()
        return data or _rt_cache_store["data"]
    except Exception:
        # 网络失败：返回上次成功结果(可能略旧),页面按DB收盘价兜底也能正常渲染
        return _rt_cache_store["data"]


def get_portfolios(rt_cache: dict[str, dict] | None = None) -> list:
    """rt_cache: 调用方可预先批量拉好的实时行情缓存（跨多个组合共用一次网络请求）。
    未传时自动批量拉取一次（而非逐组合各自拉取）。"""
    if rt_cache is None:
        rt_cache = fetch_rt_cache_for_all_portfolios()
    # 只读连接：WAL 下读永不被批任务写锁阻塞(此前用 write 连接+_ensure_tables
    # 会拿写锁,批任务跑时该读接口被 database is locked 打成 500 → 前端0组合)
    conn = connect_db(ro=True)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM portfolios ORDER BY created_at DESC").fetchall()]
    for r in rows:
        r["total_value"] = calc_total_value(r["id"], _conn=conn, rt_cache=rt_cache)
    conn.close()
    return rows


def _fetch_prev_closes(conn: sqlite3.Connection, stock_ids: list[int]) -> dict[int, float]:
    """各股票上一交易日收盘价（用于今日盈亏）。"""
    if not stock_ids:
        return {}
    placeholders = ",".join("?" * len(stock_ids))
    rows = conn.execute(
        f"""SELECT stock_id, close FROM (
               SELECT stock_id, close,
                      ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY trade_date DESC) AS rn
               FROM stock_daily_quotes
               WHERE stock_id IN ({placeholders}) AND close IS NOT NULL
            ) WHERE rn = 2""",
        stock_ids,
    ).fetchall()
    return {int(r[0]): float(r[1]) for r in rows if r[1] is not None}


def _portfolio_account_metrics(
    cash: float,
    initial_cash: float,
    holdings_mv: float,
    journal_rows: list[dict],
) -> dict[str, float]:
    """账户累计（含已实现）与当前持仓市值。"""
    from services.portfolio_analytics import _journal_stats

    total_value = round(float(cash or 0) + float(holdings_mv or 0), 2)
    initial = float(initial_cash or 0)
    account_pnl = round(total_value - initial, 2)
    account_pnl_pct = round(account_pnl / initial * 100, 2) if initial > 0 else 0.0
    realized_pnl = round(float(_journal_stats(journal_rows).get("realized_pnl") or 0), 2)
    return {
        "total_value": total_value,
        "cash": round(float(cash or 0), 2),
        "initial_cash": initial,
        "account_pnl": account_pnl,
        "account_pnl_pct": account_pnl_pct,
        "realized_pnl": realized_pnl,
    }


def get_pnl_summary(rt_cache: dict[str, dict] | None = None) -> dict:
    """所有组合的盈亏摘要（累计 / 今日 / 去重持仓 / 交易成本说明）。"""
    if rt_cache is None:
        rt_cache = fetch_rt_cache_for_all_portfolios()
    conn = connect_db(ro=True)
    conn.row_factory = sqlite3.Row
    portfolios = [
        dict(r)
        for r in conn.execute(
            "SELECT id, name, cash, initial_cash FROM portfolios ORDER BY created_at DESC"
        ).fetchall()
    ]
    all_positions = conn.execute(
        """SELECT pp.portfolio_id, pp.stock_id, pp.shares, pp.avg_cost, pp.buy_date,
                  s.code, s.name
           FROM portfolio_positions pp JOIN stocks s ON pp.stock_id = s.id"""
    ).fetchall()
    journal_by_pf: dict[int, list[dict]] = {}
    for row in conn.execute(
        "SELECT * FROM trade_journal ORDER BY portfolio_id, trade_date, id"
    ).fetchall():
        j = dict(row)
        journal_by_pf.setdefault(int(j["portfolio_id"]), []).append(j)
    ctx = get_market_context(conn)
    calendar_date = ctx.calendar_date
    trade_date = get_effective_trade_date(ctx)
    stock_ids = sorted({int(r["stock_id"]) for r in all_positions})
    prev_closes = _fetch_prev_closes(conn, stock_ids)
    conn.close()

    code_pairs_all = [(r["code"], r["stock_id"]) for r in all_positions]
    if code_pairs_all:
        conn2 = sqlite3.connect(DB_PATH)
        try:
            mark_prices = resolve_mark_prices_with_meta(code_pairs_all, conn2, rt_cache=rt_cache)
        finally:
            conn2.close()
    else:
        mark_prices = {}

    pf_names = {p["id"]: p.get("name", "") for p in portfolios}
    by_portfolio: dict[int, list] = {}
    flat_for_dedupe: list[dict] = []

    for r in all_positions:
        pid = r["portfolio_id"]
        sid = int(r["stock_id"])
        meta = mark_prices.get(r["code"], {})
        price = meta.get("price", 0) or 0
        pos = build_position_pnl(
            code=r["code"],
            name=r["name"],
            stock_id=sid,
            shares=r["shares"] or 0,
            avg_cost=r["avg_cost"] or 0,
            buy_date=r["buy_date"] or "",
            price=price,
            prev_close=prev_closes.get(sid),
            calendar_date=calendar_date,
            trade_date=trade_date,
        )
        by_portfolio.setdefault(pid, []).append(pos)
        flat_for_dedupe.append({**pos, "portfolio_name": pf_names.get(pid, "")})

    friction_pct = estimated_buy_friction_pct()

    result = []
    for pf in portfolios:
        positions = by_portfolio.get(pf["id"], [])
        totals = aggregate_totals(positions, friction_pct=friction_pct)
        all_bought_today = bool(positions) and all(p.get("bought_today") for p in positions)
        account = _portfolio_account_metrics(
            pf.get("cash") or 0,
            pf.get("initial_cash") or 0,
            totals["total_market_value"],
            journal_by_pf.get(int(pf["id"]), []),
        )
        result.append({
            "id": pf["id"],
            "name": pf.get("name", ""),
            "position_count": totals["position_count"],
            "total_cost": totals["total_cost"],
            "total_market_value": totals["total_market_value"],
            "total_pnl": totals["total_pnl"],
            "total_pnl_pct": totals["total_pnl_pct"],
            "market_pnl_pct": totals["market_pnl_pct"],
            "today_pnl_pct": totals["today_pnl_pct"],
            "display_pnl_pct": totals["display_pnl_pct"],
            "is_friction_only": totals["is_friction_only"],
            "all_bought_today": all_bought_today,
            "positions": positions,
            **account,
        })

    deduped = dedupe_positions(
        flat_for_dedupe, prev_closes=prev_closes, calendar_date=calendar_date
    )
    strategy_totals = aggregate_totals(flat_for_dedupe, friction_pct=friction_pct)
    return {
        "portfolios": result,
        "deduped": aggregate_totals(deduped, friction_pct=friction_pct) | {
            "stock_count": len(deduped),
            "positions": deduped,
        },
        "strategy_aggregate": {
            **strategy_totals,
            "portfolio_count": len(result),
            "note": "各策略持仓简单相加，同一股票在多策略中会重复计入",
        },
        "meta": {
            "calendar_date": calendar_date,
            "trade_date": trade_date,
            "friction_pct": friction_pct,
            "friction_note": (
                f"含成本口径因滑点+佣金约上浮 {friction_pct:.2f}%；"
                "卡片主数字为真实市价涨跌，≈0% 表示股价几乎未动"
            ),
        },
    }


def delete_portfolio(portfolio_id: int) -> dict:
    conn = connect_db(write=True)
    try:
        with conn:  # 原子删除：任一步失败自动 ROLLBACK
            conn.execute("DELETE FROM portfolio_lots WHERE portfolio_id=?", (portfolio_id,))
            conn.execute("DELETE FROM trade_journal WHERE portfolio_id=?", (portfolio_id,))
            conn.execute("DELETE FROM portfolio_positions WHERE portfolio_id=?", (portfolio_id,))
            conn.execute("DELETE FROM portfolio_snapshots WHERE portfolio_id=?", (portfolio_id,))
            conn.execute("DELETE FROM portfolios WHERE id=?", (portfolio_id,))
    finally:
        conn.close()
    return {"deleted": portfolio_id}


def rename_portfolio(portfolio_id: int, name: str) -> dict:
    conn = connect_db(write=True)
    _ensure_tables(conn)
    pf = conn.execute("SELECT id FROM portfolios WHERE id=?", (portfolio_id,)).fetchone()
    if not pf:
        conn.close()
        return {"error": "组合不存在"}
    conn.execute("UPDATE portfolios SET name=? WHERE id=?", (name, portfolio_id))
    conn.commit()
    conn.close()
    return {"id": portfolio_id, "name": name}


def get_portfolio(portfolio_id: int, rt_cache: dict[str, dict] | None = None) -> dict:
    """rt_cache: 调用方可预先批量拉好的实时行情缓存（跨多个组合共用一次网络请求）。"""
    conn = connect_db(write=True)
    conn.row_factory = sqlite3.Row
    _ensure_tables(conn)
    pf = conn.execute("SELECT * FROM portfolios WHERE id=?", (portfolio_id,)).fetchone()
    if not pf:
        conn.close()
        return {"error": "组合不存在"}
    pf = dict(pf)
    ctx = get_market_context(conn)
    calendar_date = ctx.calendar_date
    trade_date = get_effective_trade_date(ctx)
    code_pairs = [
        (r["code"], r["stock_id"])
        for r in conn.execute(
            """SELECT pp.stock_id, s.code FROM portfolio_positions pp
               JOIN stocks s ON pp.stock_id = s.id WHERE pp.portfolio_id=?""",
            (portfolio_id,),
        ).fetchall()
    ]
    mark_prices = (
        resolve_mark_prices_with_meta(code_pairs, conn, rt_cache=rt_cache) if code_pairs else {}
    )
    positions = [
        dict(r)
        for r in conn.execute(
            """SELECT pp.*, s.code, s.name
               FROM portfolio_positions pp JOIN stocks s ON pp.stock_id=s.id
               WHERE pp.portfolio_id=?""",
            (portfolio_id,),
        ).fetchall()
    ]
    stock_ids = sorted({int(p["stock_id"]) for p in positions})
    prev_closes = _fetch_prev_closes(conn, stock_ids)
    friction_pct = estimated_buy_friction_pct()
    for p in positions:
        sid = p["stock_id"]
        total = int(p["shares"] or 0)
        meta = mark_prices.get(p["code"], {})
        price = meta.get("price", 0) or 0
        p["price"] = price
        p["price_source"] = meta.get("source")
        p["quote_date"] = meta.get("quote_date")
        sellable = _sellable_shares(
            conn, portfolio_id, sid, apply_t1=True,
            as_of_date=trade_date,
        )
        enriched = build_position_pnl(
            code=p["code"],
            name=p["name"],
            stock_id=sid,
            shares=total,
            avg_cost=p["avg_cost"] or 0,
            buy_date=p.get("buy_date") or "",
            price=price,
            prev_close=prev_closes.get(sid),
            calendar_date=calendar_date,
            trade_date=trade_date,
        )
        p["market_value"] = enriched["market_value"]
        p["cost"] = enriched["cost"]
        p["pnl"] = enriched["pnl"]
        p["pnl_pct"] = enriched["pnl_pct"]
        p["market_pnl_pct"] = enriched["market_pnl_pct"]
        p["today_pnl_pct"] = enriched["today_pnl_pct"]
        p["display_pnl_pct"] = enriched["display_pnl_pct"]
        p["is_friction_only"] = enriched["is_friction_only"]
        p["bought_today"] = enriched["bought_today"]
        p["sellable_shares"] = sellable
        p["t1_locked"] = max(0, total - sellable)
        p["weight_pct"] = 0.0

    journal_all = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM trade_journal WHERE portfolio_id=? ORDER BY trade_date, id",
            (portfolio_id,),
        ).fetchall()
    ]
    from services.portfolio_analytics import _journal_stats

    journal_stats = _journal_stats(journal_all)

    journal = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM trade_journal WHERE portfolio_id=? ORDER BY id DESC LIMIT 50",
            (portfolio_id,),
        ).fetchall()
    ]
    history = [
        dict(r)
        for r in conn.execute(
            "SELECT snapshot_date, total_value FROM portfolio_snapshots WHERE portfolio_id=? ORDER BY snapshot_date",
            (portfolio_id,),
        ).fetchall()
    ]
    pf["pricing"] = pricing_context_dict(conn)
    conn.close()
    pf["positions"] = positions
    pf["total_value"] = round(pf["cash"] + sum(p["market_value"] for p in positions), 2)
    pf["pnl"] = round(pf["total_value"] - pf["initial_cash"], 2)
    pf["pnl_pct"] = round(pf["pnl"] / pf["initial_cash"] * 100, 2)
    pf["account_pnl"] = pf["pnl"]
    pf["account_pnl_pct"] = pf["pnl_pct"]
    unrealized = aggregate_totals(positions, friction_pct=friction_pct)
    pf["unrealized_pnl"] = unrealized["total_pnl"]
    pf["unrealized_pnl_pct"] = unrealized["total_pnl_pct"]
    pf["market_unrealized_pnl_pct"] = unrealized["market_pnl_pct"]
    pf["display_unrealized_pnl_pct"] = unrealized["display_pnl_pct"]
    pf["is_unrealized_friction_only"] = unrealized["is_friction_only"]
    pf["realized_pnl"] = round(float(journal_stats.get("realized_pnl") or 0), 2)
    if pf["total_value"] > 0:
        for p in positions:
            p["weight_pct"] = round(p["market_value"] / pf["total_value"] * 100, 2)
    pf["journal"] = journal
    pf["history"] = history
    pf["settings"] = _portfolio_settings(pf)
    return pf


def _close(c: sqlite3.Connection, external: bool) -> None:
    if not external:
        c.close()


def _record_snapshot(conn: sqlite3.Connection, portfolio_id: int) -> None:
    snap_date = get_effective_trade_date(get_market_context(conn))
    tv = calc_total_value(portfolio_id, _conn=conn)
    conn.execute(
        "INSERT OR REPLACE INTO portfolio_snapshots (portfolio_id, snapshot_date, total_value) VALUES (?,?,?)",
        (portfolio_id, snap_date, tv),
    )


def trade(
    portfolio_id: int,
    code: str,
    action: str,
    shares: int,
    apply_t1: bool = True,
    skip_risk: bool = False,
    reason: str | None = None,
    _conn: Optional[sqlite3.Connection] = None,
    price_mode: str = "auto",
    as_of_trade_date: str | None = None,
) -> dict:
    external = _conn is not None
    conn = _conn or connect_db(write=True)
    conn.row_factory = sqlite3.Row
    _ensure_tables(conn)
    pf = conn.execute("SELECT * FROM portfolios WHERE id=?", (portfolio_id,)).fetchone()
    if not pf:
        _close(conn, external)
        return {"error": "组合不存在"}
    pf = dict(pf)
    settings = _portfolio_settings(pf)

    action = action.lower().strip()
    if action not in ("buy", "sell"):
        _close(conn, external)
        return {"error": "action 须为 buy 或 sell"}

    if skip_risk and not (reason or "").strip():
        _close(conn, external)
        return {"error": "skip_risk=True 必须提供 reason"}

    # skip_risk 日志前缀：journal 中可审计
    if skip_risk and reason:
        reason = f"[SKIP_RISK] {reason.strip()}"

    err = validate_shares(shares)
    if err:
        _close(conn, external)
        return {"error": err}

    stock = conn.execute(
        "SELECT id, code, name FROM stocks WHERE code=? AND is_active=1", (code,)
    ).fetchone()
    if not stock:
        _close(conn, external)
        return {"error": "股票不在跟踪列表"}
    sid = stock["id"]
    ctx = get_market_context(conn)
    tq = resolve_trade_price(
        code, sid, action, conn, ctx=ctx,
        price_mode=price_mode,  # type: ignore[arg-type]
        as_of_trade_date=as_of_trade_date,
    )
    if tq.error:
        _close(conn, external)
        return {"error": tq.error}
    price = tq.price
    today = tq.trade_date
    amount = shares * price
    commission = 0.0
    tax = 0.0
    cash_delta = 0.0

    if action == "buy":
        cash_delta, commission, tax = split_cost(amount, "buy")
        if -cash_delta > pf["cash"]:
            max_sh = int(pf["cash"] / (price * (1 + COMMISSION)) / LOT_SIZE) * LOT_SIZE
            if max_sh < LOT_SIZE:
                _close(conn, external)
                return {"error": "资金不足"}
            shares = max_sh
            amount = shares * price
            cash_delta, commission, tax = split_cost(amount, "buy")

        cash_after = pf["cash"] + cash_delta
        if not skip_risk:
            risk_err = _check_buy_risk(conn, portfolio_id, sid, shares, price, cash_after, settings)
            if risk_err:
                _close(conn, external)
                return {"error": risk_err}

        pf["cash"] += cash_delta
        basis = cost_basis_price(price)
        conn.execute(
            """INSERT INTO portfolio_lots (portfolio_id, stock_id, shares, avg_cost, buy_date)
               VALUES (?,?,?,?,?)""",
            (portfolio_id, sid, shares, basis, today),
        )
        _sync_positions_from_lots(conn, portfolio_id)
        conn.execute(
            """INSERT INTO trade_journal
               (portfolio_id, stock_id, action, shares, price, trade_date, code, name,
                commission, tax, cash_delta, price_source, quote_date, raw_price, reason)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                portfolio_id, sid, "BUY", shares, price, today, code, stock["name"],
                commission, tax, cash_delta, tq.source, tq.quote_date, tq.raw_price, reason,
            ),
        )

    else:
        sellable = _sellable_shares(
            conn, portfolio_id, sid, apply_t1, as_of_date=get_effective_trade_date(ctx),
        )
        if shares > sellable:
            _close(conn, external)
            if apply_t1 and sellable < shares:
                return {"error": f"T+1：可卖 {sellable} 股（当日买入不可卖）"}
            return {"error": "持仓不足"}
        try:
            _sell_from_lots(conn, portfolio_id, sid, shares, apply_t1)
        except ValueError:
            _close(conn, external)
            return {"error": "持仓不足"}
        cash_delta, commission, tax = split_cost(amount, "sell")
        pf["cash"] += cash_delta
        _sync_positions_from_lots(conn, portfolio_id)
        conn.execute(
            """INSERT INTO trade_journal
               (portfolio_id, stock_id, action, shares, price, trade_date, code, name,
                commission, tax, cash_delta, price_source, quote_date, raw_price, reason)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                portfolio_id, sid, "SELL", shares, price, today, code, stock["name"],
                commission, tax, cash_delta, tq.source, tq.quote_date, tq.raw_price, reason,
            ),
        )

    conn.execute("UPDATE portfolios SET cash=? WHERE id=?", (round(pf["cash"], 2), portfolio_id))
    _record_snapshot(conn, portfolio_id)
    if not external:
        conn.commit()
    _close(conn, external)
    return {
        "portfolio_id": portfolio_id,
        "action": action,
        "code": code,
        "shares": shares,
        "price": price,
        "raw_price": tq.raw_price,
        "quote_date": tq.quote_date,
        "price_source": tq.source,
        "price_label": tq.label,
        "market_mode": tq.market_mode,
        "slippage_pct": tq.slippage_pct,
        "trade_date": today,
        "amount": round(amount, 2),
        "commission": commission,
        "tax": tax,
        "cash_delta": round(cash_delta, 2),
    }


def _rebalance_quote_price(
    conn: sqlite3.Connection,
    code: str,
    price_col: str,
    prefer_date: str | None = None,
) -> float | None:
    if prefer_date:
        row = conn.execute(
            f"""SELECT q.{price_col} FROM stock_daily_quotes q
                JOIN stocks s ON q.stock_id=s.id
                WHERE s.code=? AND q.trade_date=? AND q.{price_col} IS NOT NULL""",
            (code, prefer_date),
        ).fetchone()
        if row and row[0] is not None:
            return float(row[0])
    row = conn.execute(
        f"""SELECT q.{price_col} FROM stock_daily_quotes q JOIN stocks s ON q.stock_id=s.id
           WHERE s.code=? AND q.{price_col} IS NOT NULL ORDER BY q.trade_date DESC LIMIT 1""",
        (code,),
    ).fetchone()
    if not row or row[0] is None:
        return None
    return float(row[0])


def build_from_top_n(
    portfolio_id: int,
    top_n: int = 5,
    min_score: float = 50,
    strategy: str = "composite",
    pos_style: str = "equal",
    combination_id: int | None = None,
    lookback: int = 20,
    sector_window: int = 5,
    per_sector: int = 2,
    price_mode: str = "auto",
    as_of_trade_date: str | None = None,
) -> dict:
    strategy = normalize_strategy_id(strategy)
    if not is_valid_strategy(strategy, combination_id=combination_id):
        return {"error": f"未知策略或缺少参数: {strategy}"}
    meta = get_meta(strategy)
    strategy_key = meta.id if meta else strategy
    if combination_id and strategy == "factor_combination":
        strategy_key = f"combo#{combination_id}"

    conn = connect_db(write=True)
    conn.row_factory = sqlite3.Row
    _ensure_tables(conn)
    pf = conn.execute("SELECT * FROM portfolios WHERE id=?", (portfolio_id,)).fetchone()
    if not pf:
        conn.close()
        return {"error": "组合不存在"}

    sell_codes_hint: set[str] = set()
    reduce_inds: list[str] = []
    if strategy == "sector_rotation":
        selected, sell_codes_hint, reduce_inds, err = select_sector_rebalance(
            conn,
            top_n=top_n,
            min_score=min_score,
            sector_window=sector_window,
            per_sector=per_sector,
        )
    else:
        selected, err = select_top_n_dicts(
            conn=conn,
            strategy=strategy,
            top_n=top_n,
            min_score=min_score,
            combination_id=combination_id,
            lookback=lookback,
            sector_window=sector_window,
            per_sector=per_sector,
        )
    conn.close()
    if err:
        return {"error": err}

    rows = [{"code": r["code"], "score": float(r["score"])} for r in selected]
    target_codes = {r["code"] for r in rows}

    trade_conn = connect_db(write=True)
    trade_conn.row_factory = sqlite3.Row
    _ensure_tables(trade_conn)

    sold: list[dict] = []
    trimmed: list[dict] = []
    bought: list = []
    skipped: list = []
    skipped_sell_t1: list[dict] = []  # 因 T+1 跳过的卖出

    today = as_of_trade_date or get_effective_trade_date(get_market_context(trade_conn))
    price_col = "open" if price_mode == "open" else "close"
    settings = _portfolio_settings(pf)
    max_w = settings.get("max_weight_pct", 30) / 100.0
    total_score = sum(float(r["score"]) for r in rows)
    if pos_style == "weighted" and total_score > 0:
        weights = {r["code"]: float(r["score"]) / total_score for r in rows}
    else:
        weights = {r["code"]: 1.0 / len(rows) for r in rows}
    for code in list(weights.keys()):
        weights[code] = min(weights[code], max_w)

    def _t1_blocked(sid: int) -> bool:
        return bool(
            trade_conn.execute(
                "SELECT 1 FROM portfolio_lots WHERE portfolio_id=? AND stock_id=? AND buy_date=? LIMIT 1",
                (portfolio_id, sid, today),
            ).fetchone()
        )

    def _dual_ma_hold_blocked(sid: int) -> bool:
        if strategy != "dual_ma":
            return False
        lot = trade_conn.execute(
            """SELECT MIN(buy_date) FROM portfolio_lots
               WHERE portfolio_id=? AND stock_id=? AND shares > 0""",
            (portfolio_id, sid),
        ).fetchone()
        if not lot or not lot[0]:
            return False
        try:
            return _count_trading_days(str(lot[0])[:10], today) < DUAL_MA_MIN_HOLD_DAYS
        except Exception:
            return False

    try:
        with trade_conn:  # all-or-nothing：任一步失败自动 ROLLBACK
            old_positions = trade_conn.execute(
                """SELECT s.code, pp.stock_id, pp.shares FROM portfolio_positions pp
                   JOIN stocks s ON pp.stock_id = s.id
                   WHERE pp.portfolio_id=? AND pp.shares > 0""",
                (portfolio_id,),
            ).fetchall()

            # ① 掉出目标名单 → 清仓
            for row in old_positions:
                code = row["code"]
                sid = row["stock_id"]
                if code in target_codes:
                    continue
                if _t1_blocked(sid):
                    skipped_sell_t1.append({"code": code, "reason": "t1_same_day_buy", "sellable": 0})
                    continue
                if _dual_ma_hold_blocked(sid):
                    skipped.append({"code": code, "reason": "dual_ma_min_hold"})
                    continue
                res = trade(
                    portfolio_id, code, "sell", int(row["shares"]),
                    apply_t1=True, _conn=trade_conn,
                    price_mode=price_mode, as_of_trade_date=as_of_trade_date,
                    reason="rebalance_exit",
                )
                if "error" not in res:
                    sold.append({"code": code, "shares": row["shares"], "reason": "rebalance_exit"})

            # ② 超配减仓：行业轮动减至目标权重，其余策略仅在突破 max_weight 时减
            holdings = trade_conn.execute(
                """SELECT s.code, pp.stock_id, pp.shares FROM portfolio_positions pp
                   JOIN stocks s ON pp.stock_id = s.id
                   WHERE pp.portfolio_id=? AND pp.shares > 0""",
                (portfolio_id,),
            ).fetchall()
            total_value = calc_total_value(portfolio_id, _conn=trade_conn)
            if total_value > 0:
                for row in holdings:
                    code = row["code"]
                    if code not in target_codes:
                        continue
                    price = _rebalance_quote_price(trade_conn, code, price_col, prefer_date=today)
                    if not price or price <= 0:
                        continue
                    shares = int(row["shares"])
                    weight = shares * price / total_value
                    if strategy == "sector_rotation":
                        cap_w = weights.get(code, max_w)
                        trim_reason = "trim_to_target"
                    else:
                        cap_w = max_w
                        trim_reason = "trim_overweight"
                    if weight <= cap_w + 1e-6:
                        continue
                    target_sh = normalize_shares(int(total_value * cap_w / price))
                    sell_sh = normalize_shares(shares - target_sh)
                    sellable = _sellable_shares(
                        trade_conn, portfolio_id, int(row["stock_id"]), True, as_of_date=today,
                    )
                    sell_sh = min(sell_sh, sellable)
                    if sell_sh < LOT_SIZE:
                        continue
                    if _t1_blocked(int(row["stock_id"])):
                        skipped_sell_t1.append({"code": code, "reason": "t1_same_day_buy", "sellable": sellable})
                        continue
                    res = trade(
                        portfolio_id, code, "sell", sell_sh,
                        apply_t1=True, _conn=trade_conn,
                        price_mode=price_mode, as_of_trade_date=as_of_trade_date,
                        reason=trim_reason,
                    )
                    if "error" not in res:
                        trimmed.append({
                            "code": code,
                            "shares": sell_sh,
                            "weight_before_pct": round(weight * 100, 2),
                            "weight_cap_pct": round(cap_w * 100, 2),
                            "reason": trim_reason,
                        })

            # ③ 欠配 / 新进目标 → 用现金补仓（不超配的不减不卖）
            total_value = calc_total_value(portfolio_id, _conn=trade_conn)
            for r in rows:
                code = r["code"]
                price = _rebalance_quote_price(trade_conn, code, price_col, prefer_date=today)
                if not price or price <= 0:
                    skipped.append({"code": code, "reason": "无行情"})
                    continue
                cur = trade_conn.execute(
                    """SELECT shares FROM portfolio_positions pp
                       JOIN stocks s ON pp.stock_id=s.id
                       WHERE pp.portfolio_id=? AND s.code=?""",
                    (portfolio_id, code),
                ).fetchone()
                cur_sh = int(cur[0]) if cur else 0
                target_val = total_value * weights[code]
                cur_val = cur_sh * price
                deficit = target_val - cur_val
                min_buy = price * LOT_SIZE * (1 + COMMISSION)
                if deficit < min_buy:
                    continue
                shares = normalize_shares(int(deficit / (price * (1 + COMMISSION))))
                if shares < LOT_SIZE:
                    skipped.append({"code": code, "reason": "缺口不足一手"})
                    continue
                res = trade(
                    portfolio_id, code, "buy", shares, _conn=trade_conn,
                    price_mode=price_mode, as_of_trade_date=as_of_trade_date,
                    reason="rebalance_topup",
                )
                if "error" in res:
                    skipped.append({"code": code, "reason": res["error"]})
                else:
                    item = {"code": code, "score": r["score"], "shares": shares}
                    if strategy == "turtle":
                        stock_row = trade_conn.execute(
                            "SELECT id FROM stocks WHERE code=?", (code,),
                        ).fetchone()
                        if stock_row:
                            stop = _set_turtle_stop(
                                trade_conn,
                                portfolio_id,
                                int(stock_row[0]),
                                float(res.get("price") or price),
                                lookback,
                            )
                            if stop is not None:
                                item["turtle_stop_price"] = stop
                    bought.append(item)
    finally:
        trade_conn.close()

    out = {
        "portfolio_id": portfolio_id,
        "bought": bought,
        "sold": sold,
        "trimmed": trimmed,
        "skipped": skipped,
        "skipped_sell_t1": skipped_sell_t1,
        "count": len(bought),
        "strategy": strategy_key,
        "pos_style": pos_style,
        "rebalance_mode": "target_weight" if strategy == "sector_rotation" else "asymmetric",
        "max_weight_pct": round(max_w * 100, 2),
    }
    if strategy == "sector_rotation":
        out["reduce_industries"] = reduce_inds
        out["target_codes"] = list(target_codes)
    return out


def calc_total_value(
    portfolio_id: int,
    _conn: Optional[sqlite3.Connection] = None,
    rt_cache: dict[str, dict] | None = None,
) -> float:
    conn = _conn or connect_db(write=True)
    _ensure_tables(conn)
    pf = conn.execute("SELECT cash FROM portfolios WHERE id=?", (portfolio_id,)).fetchone()
    if not pf:
        if _conn is None:
            conn.close()
        return 0
    cash = pf[0]
    code_pairs = [
        (r[1], r[0])
        for r in conn.execute(
            """SELECT pp.stock_id, s.code FROM portfolio_positions pp
               JOIN stocks s ON pp.stock_id=s.id WHERE pp.portfolio_id=?""",
            (portfolio_id,),
        ).fetchall()
    ]
    marks = resolve_mark_prices(code_pairs, conn, rt_cache=rt_cache) if code_pairs else {}
    sid_to_code = {sid: code for code, sid in code_pairs}
    positions = conn.execute(
        "SELECT stock_id, shares FROM portfolio_positions WHERE portfolio_id=?",
        (portfolio_id,),
    ).fetchall()
    if _conn is None:
        conn.close()
    return round(
        cash + sum(
            shares * marks.get(sid_to_code.get(sid, ""), 0)
            for sid, shares in positions
        ),
        2,
    )


def snapshot_all_portfolios() -> dict:
    """每日 mark-to-market — 供 scheduler 调用"""
    conn = connect_db(write=True)
    _ensure_tables(conn)
    today = date.today().strftime("%Y-%m-%d")
    ids = [r[0] for r in conn.execute("SELECT id FROM portfolios").fetchall()]
    updated = 0
    for pid in ids:
        tv = calc_total_value(pid, _conn=conn)
        conn.execute(
            "INSERT OR REPLACE INTO portfolio_snapshots (portfolio_id, snapshot_date, total_value) VALUES (?,?,?)",
            (pid, today, tv),
        )
        updated += 1
    conn.commit()
    conn.close()
    return {"updated": updated, "date": today}


def _turtle_exit_reason(
    series: dict,
    dates: list[str],
    *,
    exit_period: int,
    stop_price: float | None,
    stop_only: bool = True,
) -> str | None:
    if len(dates) < 2:
        return None
    di = len(dates) - 1
    dt = dates[di]
    if dt not in series:
        return None
    close = float(series[dt].get("close") or 0)
    if close <= 0:
        return None
    if stop_price is not None and close < stop_price:
        return "stop"
    if stop_only:
        return None
    if len(dates) < exit_period + 2:
        return None
    prior = dates[di - exit_period : di]
    lows: list[float] = []
    for d in prior:
        if d not in series:
            continue
        lo = float(series[d].get("low") or series[d].get("close") or 0)
        if lo > 0:
            lows.append(lo)
    if len(lows) < exit_period * 0.6:
        return None
    if close < min(lows):
        return "channel"
    return None


def _scan_turtle_exits_for_portfolio(
    conn: sqlite3.Connection,
    pf: sqlite3.Row | dict,
) -> dict:
    d = dict(pf)
    pid = int(d["id"])
    lookback = int(d.get("default_lookback") or 20)
    exit_period = max(10, lookback // 2)
    need_bars = lookback + exit_period + 5
    ctx = get_market_context(conn)
    as_of = get_effective_trade_date(ctx)

    signals: list[dict] = []
    monitored: list[dict] = []
    positions = conn.execute(
        """SELECT pp.stock_id, pp.shares, pp.turtle_stop_price, s.code, s.name
           FROM portfolio_positions pp
           JOIN stocks s ON pp.stock_id = s.id
           WHERE pp.portfolio_id=? AND pp.shares > 0""",
        (pid,),
    ).fetchall()
    for pos in positions:
        series, dates = _quote_series_for_turtle(conn, int(pos["stock_id"]), need_bars)
        stop = float(pos["turtle_stop_price"]) if pos["turtle_stop_price"] is not None else None
        reason = _turtle_exit_reason(
            series, dates, exit_period=exit_period, stop_price=stop,
        )
        sellable = _sellable_shares(
            conn, pid, int(pos["stock_id"]), apply_t1=True, as_of_date=as_of,
        )
        price = float(series.get(dates[-1], {}).get("close") or 0) if dates else 0
        row = {
            "code": pos["code"],
            "name": pos["name"],
            "shares": int(pos["shares"]),
            "sellable_shares": sellable,
            "price": round(price, 2) if price else None,
            "stop_price": stop,
            "exit_reason": reason,
        }
        monitored.append(row)
        if reason:
            signals.append(row)

    return {
        "portfolio_id": pid,
        "lookback": lookback,
        "exit_period": exit_period,
        "monitored": len(monitored),
        "signal_count": len(signals),
        "signals": signals,
        "monitored_positions": monitored,
    }


def preview_turtle_exits(portfolio_id: int) -> dict:
    """检查海龟出场信号（不执行卖出）。"""
    conn = connect_db(write=True)
    conn.row_factory = sqlite3.Row
    _ensure_tables(conn)
    pf = conn.execute("SELECT * FROM portfolios WHERE id=?", (portfolio_id,)).fetchone()
    if not pf:
        conn.close()
        return {"error": "组合不存在"}
    out = _scan_turtle_exits_for_portfolio(conn, pf)
    conn.close()
    return out


def queue_turtle_exits(portfolio_id: int | None = None) -> dict:
    """海龟出场：收盘扫描信号，下一交易日开盘执行。"""
    conn = connect_db(write=True)
    conn.row_factory = sqlite3.Row
    _ensure_tables(conn)

    if portfolio_id:
        portfolios = conn.execute(
            "SELECT * FROM portfolios WHERE id=?", (portfolio_id,),
        ).fetchall()
    else:
        portfolios = conn.execute(
            "SELECT * FROM portfolios WHERE default_strategy='turtle'",
        ).fetchall()

    signal_date = date.today().isoformat()
    execute_date = _exec_date_after_signal()
    queued: list[dict] = []
    for pf in portfolios:
        scan = _scan_turtle_exits_for_portfolio(conn, pf)
        pid = int(pf["id"])
        for sig in scan["signals"]:
            sell_sh = min(int(sig["shares"]), int(sig.get("sellable_shares") or 0))
            if sell_sh < LOT_SIZE:
                continue
            dup = conn.execute(
                """SELECT 1 FROM portfolio_pending_orders
                   WHERE portfolio_id=? AND kind='turtle_sell' AND execute_date=? AND code=?
                     AND status='pending'""",
                (pid, execute_date, sig["code"]),
            ).fetchone()
            if dup:
                continue
            conn.execute(
                """INSERT INTO portfolio_pending_orders
                   (portfolio_id, kind, signal_date, execute_date, code, shares, reason)
                   VALUES (?,?,?,?,?,?,?)""",
                (pid, "turtle_sell", signal_date, execute_date, sig["code"], sell_sh, "turtle_exit"),
            )
            queued.append(
                {
                    "portfolio_id": pid,
                    "code": sig["code"],
                    "name": sig["name"],
                    "shares": sell_sh,
                    "execute_date": execute_date,
                    "exit_reason": sig.get("exit_reason"),
                }
            )

    conn.commit()
    conn.close()
    return {"queued": len(queued), "execute_date": execute_date, "details": queued}


def run_turtle_exits(portfolio_id: int | None = None) -> dict:
    """兼容旧接口：改为收盘入队，次日开盘成交。"""
    return queue_turtle_exits(portfolio_id=portfolio_id)


def _scan_momentum_crashes_for_portfolio(
    conn: sqlite3.Connection,
    pf: sqlite3.Row | dict,
) -> dict:
    pid = int(dict(pf)["id"])
    ctx = get_market_context(conn)
    as_of = get_effective_trade_date(ctx)
    signals: list[dict] = []
    positions = conn.execute(
        """SELECT pp.stock_id, pp.shares, s.code, s.name
           FROM portfolio_positions pp
           JOIN stocks s ON pp.stock_id = s.id
           WHERE pp.portfolio_id=? AND pp.shares > 0""",
        (pid,),
    ).fetchall()
    for pos in positions:
        qrows = conn.execute(
            """SELECT trade_date, close, volume FROM stock_daily_quotes
               WHERE stock_id=? AND close IS NOT NULL
               ORDER BY trade_date DESC LIMIT 25""",
            (int(pos["stock_id"]),),
        ).fetchall()
        reason = momentum_crash_reason(qrows)
        if not reason:
            continue
        sellable = _sellable_shares(
            conn, pid, int(pos["stock_id"]), apply_t1=True, as_of_date=as_of,
        )
        signals.append(
            {
                "code": pos["code"],
                "name": pos["name"],
                "shares": int(pos["shares"]),
                "sellable_shares": sellable,
                "exit_reason": reason,
            }
        )
    return {"portfolio_id": pid, "signals": signals, "signal_count": len(signals)}


def queue_momentum_crashes(portfolio_id: int | None = None) -> dict:
    """动量崩溃：收盘扫描，下一交易日开盘执行。"""
    conn = connect_db(write=True)
    conn.row_factory = sqlite3.Row
    _ensure_tables(conn)

    if portfolio_id:
        portfolios = conn.execute(
            "SELECT * FROM portfolios WHERE id=?", (portfolio_id,),
        ).fetchall()
    else:
        portfolios = conn.execute(
            "SELECT * FROM portfolios WHERE default_strategy='momentum'",
        ).fetchall()

    signal_date = date.today().isoformat()
    execute_date = _exec_date_after_signal()
    queued: list[dict] = []
    for pf in portfolios:
        scan = _scan_momentum_crashes_for_portfolio(conn, pf)
        pid = int(pf["id"])
        for sig in scan["signals"]:
            sell_sh = min(int(sig["shares"]), int(sig.get("sellable_shares") or 0))
            if sell_sh < LOT_SIZE:
                continue
            dup = conn.execute(
                """SELECT 1 FROM portfolio_pending_orders
                   WHERE portfolio_id=? AND kind='momentum_sell' AND execute_date=? AND code=?
                     AND status='pending'""",
                (pid, execute_date, sig["code"]),
            ).fetchone()
            if dup:
                continue
            conn.execute(
                """INSERT INTO portfolio_pending_orders
                   (portfolio_id, kind, signal_date, execute_date, code, shares, reason)
                   VALUES (?,?,?,?,?,?,?)""",
                (pid, "momentum_sell", signal_date, execute_date, sig["code"], sell_sh, "momentum_crash"),
            )
            queued.append(
                {
                    "portfolio_id": pid,
                    "code": sig["code"],
                    "name": sig["name"],
                    "shares": sell_sh,
                    "execute_date": execute_date,
                }
            )

    conn.commit()
    conn.close()
    return {"queued": len(queued), "execute_date": execute_date, "details": queued}


def run_momentum_crashes(portfolio_id: int | None = None) -> dict:
    return queue_momentum_crashes(portfolio_id=portfolio_id)


def sync_turtle_settings(
    portfolio_id: int,
    *,
    lookback: int = 20,
    top_n: int = 5,
    min_score: float = 60.0,
    rebalance_schedule: str = "none",
) -> dict:
    """将海龟策略预设写入组合默认参数（仅入场 + 日频止损，无定时调仓）。"""
    pf = get_portfolio(portfolio_id)
    if "error" in pf:
        return pf

    update_portfolio_settings(
        portfolio_id,
        default_strategy="turtle",
        default_lookback=lookback,
        default_top_n=top_n,
        default_min_score=min_score,
        default_pos_style="equal",
        rebalance_schedule="none",
    )

    preview, err = select_top_n_dicts(
        strategy="turtle",
        top_n=top_n,
        min_score=min_score,
        lookback=lookback,
    )
    updated = get_portfolio(portfolio_id)
    return {
        "portfolio_id": portfolio_id,
        "portfolio": updated,
        "lookback": lookback,
        "preview_buy": preview,
        "pick_error": err,
    }


def sync_sector_rotation_settings(
    portfolio_id: int,
    *,
    window_days: int | None = None,
    per_sector: int = 2,
    top_n: int = 10,
    min_score: float = 0.0,
) -> dict:
    """将 Dashboard 行业轮动信号同步到组合默认策略参数。"""
    from services.sector_rotation import compute_sector_rotation_signals
    from services.strategy_selector import select_sector_rebalance

    pf = get_portfolio(portfolio_id)
    if "error" in pf:
        return pf

    sig = compute_sector_rotation_signals(window_days=window_days or 5, force=True)
    if sig.get("error"):
        return {"error": sig["error"]}

    wd = int(sig.get("window_trading_days") or window_days or 5)
    update_portfolio_settings(
        portfolio_id,
        default_strategy="sector_rotation",
        default_sector_window=wd,
        default_per_sector=per_sector,
        default_top_n=top_n,
        default_min_score=min_score,
    )

    conn = connect_db(write=True)
    conn.row_factory = sqlite3.Row
    buys, sell_codes, reduce_inds, pick_err = select_sector_rebalance(
        conn,
        top_n=top_n,
        min_score=min_score,
        sector_window=wd,
        per_sector=per_sector,
    )
    conn.close()

    updated = get_portfolio(portfolio_id)
    return {
        "portfolio_id": portfolio_id,
        "portfolio": updated,
        "window_days": wd,
        "add_sectors": [s.get("industry") for s in (sig.get("add") or [])],
        "reduce_sectors": [s.get("industry") for s in (sig.get("reduce") or [])],
        "preview_buy": buys,
        "preview_sell_codes": sell_codes,
        "preview_reduce_industries": reduce_inds,
        "pick_error": pick_err,
    }


def update_portfolio_settings(portfolio_id: int, **kwargs) -> dict:
    conn = connect_db(write=True)
    _ensure_tables(conn)
    pf = conn.execute("SELECT id FROM portfolios WHERE id=?", (portfolio_id,)).fetchone()
    if not pf:
        conn.close()
        return {"error": "组合不存在"}
    allowed = {
        "rebalance_schedule", "max_weight_pct", "min_cash_pct",
        "default_strategy", "default_top_n", "default_min_score", "default_pos_style",
        "default_combination_id", "default_lookback",
        "default_sector_window", "default_per_sector", "name",
    }
    sets, vals, keys = [], [], []
    for k, v in kwargs.items():
        if k in allowed and v is not None:
            sets.append(f"{k}=?")
            vals.append(v)
            keys.append(k)
    strat_row = conn.execute(
        "SELECT default_strategy FROM portfolios WHERE id=?", (portfolio_id,),
    ).fetchone()
    eff_strategy = normalize_strategy_id(
        kwargs.get("default_strategy") or (strat_row[0] if strat_row else "") or "composite",
    )
    if eff_strategy == "turtle":
        sets = [s for i, s in enumerate(sets) if keys[i] != "rebalance_schedule"]
        vals = [v for i, v in enumerate(vals) if keys[i] != "rebalance_schedule"]
        sets.append("rebalance_schedule=?")
        vals.append("none")
    if sets:
        vals.append(portfolio_id)
        conn.execute(f"UPDATE portfolios SET {', '.join(sets)} WHERE id=?", vals)
        conn.commit()
    conn.close()
    return get_portfolio(portfolio_id)


def _count_trading_days(from_date: str, to_date: str) -> int:
    """统计 (from_date, to_date] 区间内的实际交易日数。"""
    conn = connect_db(write=True)
    n = conn.execute(
        """SELECT COUNT(DISTINCT trade_date) FROM stock_daily_quotes
           WHERE trade_date > ? AND trade_date <= ?""",
        (from_date, to_date),
    ).fetchone()[0]
    conn.close()
    return int(n)


def rebalance_preview(portfolio_id: int) -> dict:
    """预告下次自动调仓的买卖计划（不执行交易）。
    返回 due_date、buy/sell 列表，以及 skip_next_rebalance 状态。
    """
    from services.strategy_selector import select_sector_rebalance, select_top_n_dicts

    today = date.today()
    today_str = today.strftime("%Y-%m-%d")
    conn = connect_db(write=True)
    conn.row_factory = sqlite3.Row
    _ensure_tables(conn)
    pf = conn.execute("SELECT * FROM portfolios WHERE id=?", (portfolio_id,)).fetchone()
    if not pf:
        conn.close()
        return {"error": "组合不存在"}

    sched = pf["rebalance_schedule"] or "none"
    if sched == "none":
        conn.close()
        return {"due": False, "reason": "未开启自动调仓"}

    last = pf["last_rebalance_date"]
    try:
        td = _count_trading_days(last[:10], today_str) if last else 999
    except Exception:
        td = 999

    threshold = 5 if sched == "weekly" else 20
    days_left = max(threshold - td, 0)
    due_soon = days_left <= 1  # 今天或明天到期

    settings = _portfolio_settings(pf)
    strategy = normalize_strategy_id(settings["default_strategy"])
    top_n = settings["default_top_n"]
    min_score = settings["default_min_score"]
    sector_window = settings.get("default_sector_window", 5)
    per_sector = settings.get("default_per_sector", 2)
    lookback = settings.get("default_lookback", 20)
    combination_id = settings.get("default_combination_id")

    # 当前持仓
    old_positions = conn.execute(
        """SELECT s.code, s.name, pp.shares FROM portfolio_positions pp
           JOIN stocks s ON pp.stock_id = s.id
           WHERE pp.portfolio_id=? AND pp.shares > 0""",
        (portfolio_id,),
    ).fetchall()
    current_codes = {r["code"] for r in old_positions}

    # 预测目标持仓（dry-run 选股）
    if strategy == "sector_rotation":
        selected, sell_hint, _, err = select_sector_rebalance(
            conn, top_n=top_n, min_score=min_score,
            sector_window=sector_window, per_sector=per_sector,
        )
    else:
        selected, err = select_top_n_dicts(
            conn=conn, strategy=strategy, top_n=top_n, min_score=min_score,
            combination_id=combination_id, lookback=lookback,
            sector_window=sector_window, per_sector=per_sector,
        )
    conn.close()

    if err:
        return {"due": due_soon, "days_left": days_left, "error": err,
                "skip_next_rebalance": bool(pf["skip_next_rebalance"])}

    target_codes = {r["code"] for r in selected}
    preview_sell = [
        {"code": r["code"], "name": r["name"], "shares": r["shares"], "reason": "rebalance_exit"}
        for r in old_positions if r["code"] not in target_codes
    ]

    conn2 = connect_db()
    conn2.row_factory = sqlite3.Row
    max_w_pct = settings.get("max_weight_pct", 30)
    pos_style = settings.get("default_pos_style", "equal")
    total_score = sum(float(r["score"]) for r in selected) or 1.0
    max_w = max_w_pct / 100.0
    weights = (
        {r["code"]: float(r["score"]) / total_score for r in selected}
        if pos_style == "weighted"
        else {r["code"]: 1.0 / len(selected) for r in selected}
    )
    for code in list(weights):
        weights[code] = min(weights[code], max_w)

    preview_trim: list[dict] = []
    tv = calc_total_value(portfolio_id, _conn=conn2)
    if tv > 0:
        for r in old_positions:
            if r["code"] not in target_codes:
                continue
            price_row = conn2.execute(
                """SELECT q.close FROM stock_daily_quotes q JOIN stocks s ON q.stock_id=s.id
                   WHERE s.code=? ORDER BY q.trade_date DESC LIMIT 1""", (r["code"],)
            ).fetchone()
            if not price_row or not price_row[0]:
                continue
            price = float(price_row[0])
            weight = int(r["shares"]) * price / tv * 100
            if strategy == "sector_rotation":
                cap_w = weights.get(r["code"], max_w)
                trim_reason = "trim_to_target"
            else:
                cap_w = max_w
                trim_reason = "trim_overweight"
            cap_pct = cap_w * 100
            if weight > cap_pct + 0.01:
                target_sh = normalize_shares(int(tv * cap_w / price))
                est_trim = normalize_shares(int(r["shares"]) - target_sh)
                if est_trim >= LOT_SIZE:
                    preview_trim.append({
                        "code": r["code"],
                        "name": r["name"],
                        "est_shares": est_trim,
                        "weight_pct": round(weight, 1),
                        "cap_pct": round(cap_pct, 1),
                        "reason": trim_reason,
                    })

    # 估算买入手数：当前现金 + 预计卖出/减仓收回资金
    pf_cash = conn2.execute("SELECT cash FROM portfolios WHERE id=?", (portfolio_id,)).fetchone()
    estimated_cash = float(pf_cash["cash"]) if pf_cash else 0.0
    for s in preview_sell + preview_trim:
        price_row = conn2.execute(
            """SELECT q.close FROM stock_daily_quotes q JOIN stocks s ON q.stock_id=s.id
               WHERE s.code=? ORDER BY q.trade_date DESC LIMIT 1""", (s["code"],)
        ).fetchone()
        if price_row:
            sh = s.get("shares") or s.get("est_shares") or 0
            estimated_cash += float(price_row[0]) * sh

    held_map = {r["code"]: r for r in old_positions}
    preview_buy = []
    if strategy == "sector_rotation":
        for r in selected:
            code = r["code"]
            price_row = conn2.execute(
                """SELECT q.close FROM stock_daily_quotes q JOIN stocks s ON q.stock_id=s.id
                   WHERE s.code=? ORDER BY q.trade_date DESC LIMIT 1""", (code,),
            ).fetchone()
            if not price_row or float(price_row[0]) <= 0:
                continue
            price = float(price_row[0])
            cur_sh = int(held_map[code]["shares"]) if code in held_map else 0
            target_val = tv * weights.get(code, 1.0 / len(selected))
            deficit = target_val - cur_sh * price
            min_buy = price * LOT_SIZE * (1 + COMMISSION)
            est_shares = None
            if deficit >= min_buy:
                est_shares = normalize_shares(int(deficit / (price * (1 + COMMISSION))))
                if est_shares < LOT_SIZE:
                    est_shares = None
            if est_shares:
                preview_buy.append({
                    "code": code,
                    "name": r.get("name", ""),
                    "score": round(float(r["score"]), 1),
                    "est_shares": est_shares,
                })
    else:
        new_stocks = [r for r in selected if r["code"] not in current_codes]
        for r in new_stocks:
            price_row = conn2.execute(
                """SELECT q.close FROM stock_daily_quotes q JOIN stocks s ON q.stock_id=s.id
                   WHERE s.code=? ORDER BY q.trade_date DESC LIMIT 1""", (r["code"],)
            ).fetchone()
            est_shares = None
            if price_row and float(price_row[0]) > 0:
                budget = estimated_cash * weights.get(r["code"], 1.0 / len(selected))
                est_shares = normalize_shares(int(budget / (float(price_row[0]) * (1 + COMMISSION))))
                if est_shares < LOT_SIZE:
                    est_shares = None
            preview_buy.append({
                "code": r["code"],
                "name": r.get("name", ""),
                "score": round(float(r["score"]), 1),
                "est_shares": est_shares,
            })
    conn2.close()

    return {
        "due": due_soon,
        "days_left": days_left,
        "schedule": sched,
        "strategy": strategy,
        "exec_timing": "next_open",
        "execute_date": _exec_date_after_signal() if due_soon else None,
        "rebalance_mode": "target_weight" if strategy == "sector_rotation" else "asymmetric",
        "max_weight_pct": max_w_pct,
        "preview_buy": preview_buy,
        "preview_sell": preview_sell,
        "preview_trim": preview_trim,
        "skip_next_rebalance": bool(pf["skip_next_rebalance"]),
    }


def set_skip_next_rebalance(portfolio_id: int, skip: bool) -> dict:
    """设置/取消跳过下次自动调仓"""
    conn = connect_db(write=True)
    conn.row_factory = sqlite3.Row
    _ensure_tables(conn)
    pf = conn.execute("SELECT id FROM portfolios WHERE id=?", (portfolio_id,)).fetchone()
    if not pf:
        conn.close()
        return {"error": "组合不存在"}
    conn.execute("UPDATE portfolios SET skip_next_rebalance=? WHERE id=?", (1 if skip else 0, portfolio_id))
    conn.commit()
    conn.close()
    return {"ok": True, "skip_next_rebalance": skip}


def reset_portfolio_holdings(portfolio_id: int) -> dict:
    """清空持仓与成交记录，恢复为全现金（用于误即时建仓后重置）。"""
    conn = connect_db(write=True)
    _ensure_tables(conn)
    pf = conn.execute(
        "SELECT initial_cash FROM portfolios WHERE id=?", (portfolio_id,),
    ).fetchone()
    if not pf:
        conn.close()
        return {"error": "组合不存在"}
    cash = float(pf[0])
    conn.execute("DELETE FROM portfolio_lots WHERE portfolio_id=?", (portfolio_id,))
    conn.execute("DELETE FROM portfolio_positions WHERE portfolio_id=?", (portfolio_id,))
    conn.execute("DELETE FROM trade_journal WHERE portfolio_id=?", (portfolio_id,))
    conn.execute(
        "UPDATE portfolios SET cash=? WHERE id=?",
        (cash, portfolio_id),
    )
    today = date.today().isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO portfolio_snapshots (portfolio_id, snapshot_date, total_value) VALUES (?,?,?)",
        (portfolio_id, today, cash),
    )
    conn.commit()
    conn.close()
    return {"portfolio_id": portfolio_id, "cash": cash, "reset": True}


def queue_build_top_n(
    portfolio_id: int,
    *,
    top_n: int = 5,
    min_score: float = 50,
    strategy: str = "composite",
    pos_style: str = "equal",
    combination_id: int | None = None,
    lookback: int = 20,
    sector_window: int = 5,
    per_sector: int = 2,
) -> dict:
    """手动建仓/调仓：收盘入队，下一交易日开盘价 + 滑点执行。"""
    strategy = normalize_strategy_id(strategy)
    if not is_valid_strategy(strategy, combination_id=combination_id):
        return {"error": f"未知策略: {strategy}"}

    update_portfolio_settings(
        portfolio_id,
        default_strategy=strategy,
        default_top_n=top_n,
        default_min_score=min_score,
        default_pos_style=pos_style,
        default_combination_id=combination_id,
        default_lookback=lookback,
        default_sector_window=sector_window,
        default_per_sector=per_sector,
    )

    today_str = date.today().isoformat()
    execute_date = _exec_date_after_signal()
    conn = connect_db(write=True)
    _ensure_tables(conn)
    dup = conn.execute(
        """SELECT 1 FROM portfolio_pending_orders
           WHERE portfolio_id=? AND kind='rebalance' AND execute_date=? AND status='pending'""",
        (portfolio_id, execute_date),
    ).fetchone()
    if dup:
        conn.close()
        return {
            "queued": 0,
            "execute_date": execute_date,
            "exec_timing": "next_open",
            "reason": "已有待执行的建仓/调仓计划",
        }
    conn.execute(
        """INSERT INTO portfolio_pending_orders
           (portfolio_id, kind, signal_date, execute_date, reason)
           VALUES (?,?,?,?,?)""",
        (portfolio_id, "rebalance", today_str, execute_date, "manual_build"),
    )
    conn.commit()
    conn.close()
    return {
        "queued": 1,
        "portfolio_id": portfolio_id,
        "execute_date": execute_date,
        "exec_timing": "next_open",
        "strategy": strategy,
        "top_n": top_n,
    }


def queue_scheduled_rebalances() -> dict:
    """检查到期调仓计划，收盘入队，下一交易日开盘执行。"""
    today = date.today()
    today_str = today.strftime("%Y-%m-%d")
    execute_date = _exec_date_after_signal(today)
    conn = connect_db(write=True)
    conn.row_factory = sqlite3.Row
    _ensure_tables(conn)
    rows = conn.execute(
        """SELECT * FROM portfolios
           WHERE rebalance_schedule IN ('weekly','monthly')
             AND COALESCE(default_strategy, '') != 'turtle'"""
    ).fetchall()

    queued: list[dict] = []
    for pf in rows:
        sched = pf["rebalance_schedule"]
        last = pf["last_rebalance_date"]
        due = False
        if not last:
            due = True
        else:
            try:
                td = _count_trading_days(last[:10], today_str)
                due = td >= 5 if sched == "weekly" else td >= 20
            except Exception:
                due = True
        if not due:
            continue
        if pf["skip_next_rebalance"]:
            conn.execute(
                "UPDATE portfolios SET skip_next_rebalance=0, last_rebalance_date=? WHERE id=?",
                (today_str, pf["id"]),
            )
            continue
        dup = conn.execute(
            """SELECT 1 FROM portfolio_pending_orders
               WHERE portfolio_id=? AND kind='rebalance' AND execute_date=? AND status='pending'""",
            (pf["id"], execute_date),
        ).fetchone()
        if dup:
            continue
        conn.execute(
            """INSERT INTO portfolio_pending_orders
               (portfolio_id, kind, signal_date, execute_date, reason)
               VALUES (?,?,?,?,?)""",
            (pf["id"], "rebalance", today_str, execute_date, sched),
        )
        conn.execute(
            "UPDATE portfolios SET last_rebalance_date=? WHERE id=?",
            (today_str, pf["id"]),
        )
        queued.append({"portfolio_id": pf["id"], "schedule": sched, "execute_date": execute_date})

    conn.commit()
    conn.close()
    return {"queued": len(queued), "execute_date": execute_date, "details": queued}


def run_scheduled_rebalances() -> dict:
    """兼容旧接口：改为收盘入队，次日开盘成交。"""
    return queue_scheduled_rebalances()


def execute_pending_orders_at_open(as_of: str | None = None, *, force: bool = False) -> dict:
    """执行待处理的自动卖单/调仓（默认按 execute_date 当日开盘价 + 滑点）。

    force=True 或存在「严格过期单」(execute_date < today) 时,绕过非交易日/开盘前守卫直接补跑——
    过期单本就该在其 execute_date 执行过,拖到今天(哪怕周末)也要补上,按各自 execute_date 开盘价成交。
    """
    from services.trade_calendar import is_trading_day

    today_str = as_of or date.today().isoformat()
    conn = connect_db(write=True)
    conn.row_factory = sqlite3.Row
    _ensure_tables(conn)
    ctx = get_market_context(conn)

    # 是否有严格过期(execute_date < today)的待办 → 有则必须补跑,不受交易日/开盘前限制
    has_overdue = conn.execute(
        "SELECT 1 FROM portfolio_pending_orders WHERE status='pending' AND execute_date < ? LIMIT 1",
        (today_str,),
    ).fetchone() is not None
    bypass = force or has_overdue

    if not is_trading_day(date.fromisoformat(today_str)) and not as_of and not bypass:
        conn.close()
        return {"executed": 0, "reason": "非交易日", "details": []}

    if ctx.mode == "closed" and ctx.session_label == "开盘前" and not as_of and not bypass:
        conn.close()
        return {"executed": 0, "reason": "开盘前暂不执行", "details": []}

    rows = conn.execute(
        """SELECT * FROM portfolio_pending_orders
           WHERE status='pending' AND execute_date <= ?
           ORDER BY CASE kind WHEN 'turtle_sell' THEN 0 WHEN 'momentum_sell' THEN 0 ELSE 1 END, id""",
        (today_str,),
    ).fetchall()

    exit_done: list[dict] = []
    rebalance_rows: list[sqlite3.Row] = []
    errors: list[dict] = []

    for row in rows:
        if row["kind"] in ("turtle_sell", "momentum_sell"):
            pid = int(row["portfolio_id"])
            exec_date = row["execute_date"]
            try:
                res = trade(
                    pid,
                    row["code"],
                    "sell",
                    int(row["shares"]),
                    apply_t1=True,
                    reason=row["reason"] or row["kind"],
                    _conn=conn,
                    price_mode="open",
                    as_of_trade_date=exec_date,
                )
            except Exception as _e:  # noqa: BLE001 — 单笔卖单异常不得中断整批、不得泄漏连接
                errors.append({"order_id": row["id"], "kind": row["kind"], "error": str(_e)[:200]})
                continue
            if "error" in res:
                errors.append({"order_id": row["id"], "kind": row["kind"], "error": res["error"]})
                continue
            conn.execute(
                """UPDATE portfolio_pending_orders
                   SET status='executed', executed_at=datetime('now', 'localtime')
                   WHERE id=?""",
                (row["id"],),
            )
            exit_done.append(
                {
                    "portfolio_id": pid,
                    "code": row["code"],
                    "shares": row["shares"],
                    "kind": row["kind"],
                    "execute_date": exec_date,
                    "price": res.get("price"),
                }
            )
        elif row["kind"] == "rebalance":
            rebalance_rows.append(row)

    if exit_done:
        conn.commit()

    rebalance_done: list[dict] = []
    rebalance_portfolios: set[int] = set()
    for row in rebalance_rows:
        pid = int(row["portfolio_id"])
        exec_date = row["execute_date"]
        if pid in rebalance_portfolios:
            continue
        rebalance_portfolios.add(pid)
        settings = _portfolio_settings(
            conn.execute("SELECT * FROM portfolios WHERE id=?", (pid,)).fetchone()
        )
        try:
            r = build_from_top_n(
                pid,
                top_n=settings["default_top_n"],
                min_score=settings["default_min_score"],
                strategy=settings["default_strategy"],
                pos_style=settings["default_pos_style"],
                combination_id=settings.get("default_combination_id"),
                lookback=settings.get("default_lookback", 20),
                sector_window=settings.get("default_sector_window", 5),
                per_sector=settings.get("default_per_sector", 2),
                price_mode="open",
                as_of_trade_date=exec_date,
            )
        except Exception as _e:  # noqa: BLE001 — 单个组合建仓异常(含 DB 锁)不得中断整批、不得泄漏连接
            errors.append({"order_id": row["id"], "kind": "rebalance", "error": str(_e)[:200]})
            continue
        if "error" in r:
            errors.append({"order_id": row["id"], "kind": "rebalance", "error": r["error"]})
            continue
        conn.execute(
            """UPDATE portfolio_pending_orders
               SET status='executed', executed_at=datetime('now', 'localtime')
               WHERE portfolio_id=? AND kind='rebalance' AND execute_date=? AND status='pending'""",
            (pid, exec_date),
        )
        rebalance_done.append(
            {"portfolio_id": pid, "execute_date": exec_date, "count": r.get("count", 0)}
        )

    conn.commit()
    conn.close()
    turtle_done = [x for x in exit_done if x.get("kind") == "turtle_sell"]
    momentum_done = [x for x in exit_done if x.get("kind") == "momentum_sell"]
    total = len(exit_done) + len(rebalance_done)
    return {
        "executed": total,
        "turtle_exits": len(turtle_done),
        "momentum_exits": len(momentum_done),
        "rebalances": len(rebalance_done),
        "details": {"turtle": turtle_done, "momentum": momentum_done, "rebalance": rebalance_done},
        "errors": errors,
    }


def score_alerts(portfolio_id: int, threshold: float = 40.0) -> dict:
    """扫描持仓中评分恶化的股票（触发式调仓信号）。
    触发条件：composite_v5 < threshold 或 veto_status = 'exclude'
    行业轮动/海龟策略有自己的换仓逻辑，跳过 V5 评分预警。
    """
    conn = connect_db(write=True)
    conn.row_factory = sqlite3.Row
    _ensure_tables(conn)
    pf = conn.execute("SELECT * FROM portfolios WHERE id=?", (portfolio_id,)).fetchone()
    if not pf:
        conn.close()
        return {"error": "组合不存在"}

    # 只有 V5 评分类策略才使用评分预警；其他策略（行业轮动/海龟/动量/因子）有自己的选股逻辑
    from services.strategy_registry import get_meta, normalize_strategy_id
    strategy = normalize_strategy_id(pf["default_strategy"] or "composite")
    meta = get_meta(strategy)
    if not meta or meta.kind != "v5":
        conn.close()
        return {"alerts": [], "checked": 0, "threshold": threshold, "skipped": strategy}

    positions = conn.execute(
        """SELECT pp.stock_id, s.code, s.name, pp.shares, pp.avg_cost
           FROM portfolio_positions pp JOIN stocks s ON pp.stock_id = s.id
           WHERE pp.portfolio_id = ? AND pp.shares > 0""",
        (portfolio_id,),
    ).fetchall()

    if not positions:
        conn.close()
        return {"alerts": [], "checked": 0, "threshold": threshold}

    # 确定用哪个维度分做预警
    from services.v5_score_query import _parse_dim_score
    from services.v5_scorer import tier_to_pct  # noqa: F401 already imported via _parse_dim_score path
    use_dim = strategy if strategy != "composite" else None  # None = 用 composite_v5 列

    stock_ids = [p["stock_id"] for p in positions]
    placeholders = ",".join("?" * len(stock_ids))
    scores = {
        r["stock_id"]: dict(r)
        for r in conn.execute(
            f"""SELECT cs.stock_id, cs.composite_v5, cs.veto_status, cs.v5_breakdown_json
                FROM comprehensive_scores cs
                WHERE cs.stock_id IN ({placeholders})
                AND cs.calc_date = (
                    SELECT MAX(calc_date) FROM comprehensive_scores WHERE composite_v5 IS NOT NULL
                )""",
            stock_ids,
        ).fetchall()
    }
    conn.close()

    # 策略维度标签（用于前端显示）
    from services.strategy_registry import STRATEGIES
    dim_labels = {k: v.label for k, v in STRATEGIES.items()}
    score_label = dim_labels.get(strategy, "V5")

    alerts = []
    for p in positions:
        sid = p["stock_id"]
        sc = scores.get(sid)
        if sc is None:
            continue
        veto = sc["veto_status"] or "ok"
        if use_dim:
            score_val = _parse_dim_score(sc["v5_breakdown_json"], use_dim)
        else:
            score_val = sc["composite_v5"]
        triggered = (score_val is not None and score_val < threshold) or veto == "exclude"
        if triggered:
            alerts.append({
                "stock_id": sid,
                "code": p["code"],
                "name": p["name"],
                "shares": p["shares"],
                "avg_cost": p["avg_cost"],
                "composite_v5": round(score_val, 1) if score_val is not None else None,
                "score_label": score_label,
                "veto_status": veto,
                "trigger": "exclude" if veto == "exclude" else "low_score",
            })

    alerts.sort(key=lambda x: (x["composite_v5"] or 0))
    return {"alerts": alerts, "checked": len(positions), "threshold": threshold, "strategy": strategy}


def replace_position(
    portfolio_id: int,
    sell_code: str,
    strategy: str = "composite",
    min_score: float = 50.0,
    combination_id: int | None = None,
    lookback: int = 20,
    sector_window: int = 5,
    per_sector: int = 2,
) -> dict:
    """卖出触发股，从 Top-N 中选最高分的未持仓股买入替换。"""
    conn = connect_db(write=True)
    conn.row_factory = sqlite3.Row
    _ensure_tables(conn)

    pf = conn.execute("SELECT * FROM portfolios WHERE id=?", (portfolio_id,)).fetchone()
    if not pf:
        conn.close()
        return {"error": "组合不存在"}

    # 当前持仓代码（排除替换目标）
    held = {
        r["code"]
        for r in conn.execute(
            """SELECT s.code FROM portfolio_positions pp JOIN stocks s ON pp.stock_id=s.id
               WHERE pp.portfolio_id=? AND pp.shares>0""",
            (portfolio_id,),
        ).fetchall()
    }

    # 卖出持仓
    sell_row = conn.execute(
        """SELECT pp.shares, pp.stock_id FROM portfolio_positions pp
           JOIN stocks s ON pp.stock_id=s.id
           WHERE pp.portfolio_id=? AND s.code=?""",
        (portfolio_id, sell_code),
    ).fetchone()
    if not sell_row or sell_row["shares"] <= 0:
        conn.close()
        return {"error": f"{sell_code} 不在持仓中"}

    # 选候选替换股（排除全部已持仓）
    strategy = normalize_strategy_id(strategy)
    selected, err = select_top_n_dicts(
        conn=conn,
        strategy=strategy,
        top_n=20,
        min_score=min_score,
        combination_id=combination_id,
        lookback=lookback,
        sector_window=sector_window,
        per_sector=per_sector,
    )
    conn.close()

    if err:
        return {"error": err}

    candidates = [r for r in selected if r["code"] not in held]
    if not candidates:
        return {"error": "无合适替换候选（所有 Top-N 均已持仓或低于阈值）"}

    buy_candidate = candidates[0]
    buy_code = buy_candidate["code"]

    # 执行卖出
    sell_result = trade(portfolio_id, sell_code, "sell", int(sell_row["shares"]))
    if "error" in sell_result:
        return {"error": f"卖出失败: {sell_result['error']}"}

    # 计算买入手数（与原仓位等市值）
    sell_proceeds = sell_result.get("cash_delta", 0)
    buy_price = sell_result.get("price", 0) or 0
    # 用卖出所得估算买入股数（对齐 100 股整数）
    buy_conn = connect_db(write=True)
    buy_conn.row_factory = sqlite3.Row
    price_meta = resolve_mark_prices_with_meta([(buy_code, None)], buy_conn)
    buy_conn.close()
    est_price = (price_meta.get(buy_code) or {}).get("price") or buy_price or 10.0
    # 保留 2% 缓冲；含佣金成本倒推可买手数
    from services.trading_rules import COMMISSION as _COMM
    if est_price > 0:
        cost_per_share = est_price * (1 + _COMM)
        raw_shares = int(abs(sell_proceeds) * 0.98 / cost_per_share)
        buy_shares = (raw_shares // 100) * 100
    else:
        buy_shares = 0
    if buy_shares < 100:
        return {"error": f"卖出所得不足以买入 {buy_code} 最小 1 手（est_price={est_price}，proceeds={sell_proceeds:.2f}）", "sell": sell_result}

    buy_result = trade(portfolio_id, buy_code, "buy", buy_shares)
    if "error" in buy_result:
        return {
            "warning": f"已卖出 {sell_code}，但买入 {buy_code} 失败: {buy_result['error']}",
            "sell": sell_result,
        }

    return {
        "sold": {"code": sell_code, "shares": int(sell_row["shares"])},
        "bought": {"code": buy_code, "name": buy_candidate.get("name", ""), "shares": buy_shares, "score": float(buy_candidate["score"])},
        "sell_result": sell_result,
        "buy_result": buy_result,
    }
