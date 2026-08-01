"""QAFactor — 因子全生命周期：入库/IC分析/分层回测/衰减/合成"""
from __future__ import annotations

import math
import sqlite3
from datetime import date

from config import DB_PATH


def init_factor_store() -> sqlite3.Connection:
    """初始化因子表"""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS factor_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            factor_id TEXT UNIQUE, name TEXT, category TEXT, formula TEXT
        );
        CREATE TABLE IF NOT EXISTS factor_values (
            stock_id INTEGER, date TEXT, factor_id TEXT,
            value REAL, rank INTEGER,
            PRIMARY KEY (stock_id, date, factor_id)
        );
    """)
    conn.commit()
    factors = [
        ("F001", "composite_score", "综合", "评分系统"),
        ("F002", "fundamental_score", "基本面", "评分系统"),
        ("F003", "technical_score", "技术面", "评分系统"),
        ("F004", "sentiment_score", "情绪面", "评分系统"),
        ("F005", "capital_score", "资金面", "评分系统"),
        ("F006", "policy_score", "政策面", "评分系统"),
        ("F007", "mood_score", "情绪", "评分系统"),
        ("F008", "val_score", "估值", "评分系统"),
        ("F009", "momentum_20d", "动量", "close/shift(20)-1"),
        ("F010", "volatility_20d", "低波", "-std(ret,20)"),
        ("F011", "volume_ratio", "量价", "vol_5d/vol_20d"),
        ("F012", "rsi_divergence", "反转", "RSI(14)"),
        ("F013", "ma_crossover", "趋势", "MA5>MA20?1:-1"),
        ("F014", "turnover_adj", "流动性", "turnover/mean(20)"),
        ("F015", "debate_final", "AI", "辩论最终分"),
        ("F016", "momentum_60d", "动量", "close/shift(60)-1"),
        ("F017", "momentum_120d", "动量", "close/shift(120)-1"),
        ("F018", "momentum_250d", "动量", "close/shift(250)-1"),
        ("F019", "reversal_5d", "反转", "-momentum_5d"),
        ("F020", "reversal_20d", "反转", "-momentum_20d"),
        ("F021", "volatility_5d", "低波", "-std(ret,5)"),
        ("F022", "volatility_60d", "低波", "-std(ret,60)"),
        ("F023", "adx_14", "趋势", "ADX(14)"),
        ("F024", "pv_corr_5d", "量价", "corr(close,volume,5)"),
        ("F025", "pv_corr_20d", "量价", "corr(close,volume,20)"),
        ("F026", "amplitude_std_120d", "波动", "std((H-L)/C,120)"),
        ("F027", "wq_alpha6", "WQ", "-corr(open,volume,10)"),
        ("F028", "wq_alpha12", "WQ", "sign(dV)*(-dC)"),
        ("F029", "margin_chg_5d", "融资", "margin/shift(5)-1"),
        ("F030", "margin_chg_20d", "融资", "margin/shift(20)-1"),
        ("F031", "ma_crossover_filtered", "趋势", "MA5/MA20+ADX+迟滞"),
    ]
    for fid, name, cat, formula in factors:
        conn.execute(
            "INSERT OR IGNORE INTO factor_registry (factor_id, name, category, formula) VALUES (?,?,?,?)",
            (fid, name, cat, formula),
        )
    conn.commit()
    return conn


def _calc_rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(period):
        diff = closes[i] - closes[i + 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def _upsert_factor(
    conn,
    sid: int,
    dt: str,
    fid: str,
    val: float,
    quality_flag: str | None = None,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO factor_values (stock_id, date, factor_id, value) VALUES (?,?,?,?)",
        (sid, dt, fid, round(val, 2)),
    )
    from services.factor_values_wide import upsert_wide_factor

    upsert_wide_factor(conn, sid, dt, fid, round(val, 2), quality_flag)


def _compute_technical_factors(conn, sid: int, dt: str, *, code: str | None = None) -> int:
    """F009-F030 技术面因子（OHLCV + 融资余额自算）"""
    from services.data_cleaner import ensure_quote_columns
    from services.ohlcv_technical_factors import compute_ohlcv_factors, load_quote_panel

    ensure_quote_columns(conn)
    if not code:
        row = conn.execute("SELECT code FROM stocks WHERE id=?", (sid,)).fetchone()
        code = row[0] if row else ""

    panel = load_quote_panel(conn, sid, code or "", dt)
    if panel["n_bars"] < 21:
        return 0

    closes = panel["closes"]
    n = 0
    for fid, val in compute_ohlcv_factors(panel).items():
        _upsert_factor(conn, sid, dt, fid, val)
        n += 1

    rsi = _calc_rsi(closes)
    _upsert_factor(conn, sid, dt, "F012", rsi)
    n += 1
    ma5 = sum(closes[:5]) / 5
    ma20 = sum(closes[:20]) / 20
    _upsert_factor(conn, sid, dt, "F013", 1 if ma5 > ma20 else -1)
    n += 1
    from services.ohlcv_technical_factors import _ma_crossover_filtered
    f031 = _ma_crossover_filtered(panel)
    if f031 is not None:
        _upsert_factor(conn, sid, dt, "F031", f031)
        n += 1
    return n


def _backfill_score_factors(conn) -> int:
    """从 comprehensive_scores 历史回填 F001-F008"""
    fid_map = {
        "composite_score": "F001",
        "fundamental_score": "F002",
        "technical_score": "F003",
        "sentiment_score": "F004",
        "capital_score": "F005",
        "policy_score": "F006",
        "mood_score": "F007",
        "val_score": "F008",
    }
    count = 0
    rows = conn.execute(
        """SELECT stock_id, calc_date, composite_score, fundamental_score, technical_score,
                  sentiment_score, capital_score, policy_score, mood_score, val_score
           FROM comprehensive_scores ORDER BY calc_date"""
    ).fetchall()
    cols = [
        "composite_score",
        "fundamental_score",
        "technical_score",
        "sentiment_score",
        "capital_score",
        "policy_score",
        "mood_score",
        "val_score",
    ]
    for row in rows:
        sid, dt = row[0], row[1]
        for i, col in enumerate(cols):
            val = row[i + 2]
            if val is None:
                continue
            fid = fid_map[col]
            quality_flag = None
            if fid == "F002":
                from services.factor_quality import filter_fundamental_for_backfill, is_factor_value_valid

                filtered = filter_fundamental_for_backfill(sid, dt, float(val))
                if filtered is None:
                    continue
                val = filtered
                _, quality_flag = is_factor_value_valid("F002", sid, dt)
            _upsert_factor(conn, sid, dt, fid, float(val), quality_flag)
            count += 1
    return count


def compute_factors(backfill: bool = True) -> dict:
    """计算全量因子值并入库（含历史回填）"""
    from services.factor_s0_setup import run_factor_s0_setup

    s0 = run_factor_s0_setup(migrate_wide=False)
    today = date.today().strftime("%Y-%m-%d")
    conn = init_factor_store()
    stocks = conn.execute("SELECT id, code FROM stocks WHERE is_active=1").fetchall()
    count = 0

    if backfill:
        count += _backfill_score_factors(conn)

    # 当日评分因子（最新 calc_date）
    cs = {
        r[0]: r
        for r in conn.execute(
            """SELECT stock_id, composite_score, fundamental_score, technical_score,
                      sentiment_score, capital_score, policy_score, mood_score, val_score
               FROM comprehensive_scores
               WHERE calc_date = (SELECT MAX(calc_date) FROM comprehensive_scores)"""
        ).fetchall()
    }
    fid_map = ["F001", "F002", "F003", "F004", "F005", "F006", "F007", "F008"]
    for sid, _ in stocks:
        if sid not in cs:
            continue
        vals = cs[sid]
        for i, fid in enumerate(fid_map):
            if vals[i] is not None:
                v = float(vals[i])
                qf = None
                if fid == "F002":
                    from services.factor_quality import filter_fundamental_for_backfill, is_factor_value_valid

                    v2 = filter_fundamental_for_backfill(sid, today, v)
                    if v2 is None:
                        continue
                    v = v2
                    _, qf = is_factor_value_valid("F002", sid, today)
                _upsert_factor(conn, sid, today, fid, v, qf)
                count += 1

    stock_codes = {r[0]: r[1] for r in stocks}
    for sid, code in stock_codes.items():
        count += _compute_technical_factors(conn, sid, today, code=code)

    deb = {}
    try:
        deb = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT stock_id, adjusted_score FROM debate_v2 WHERE date=(SELECT MAX(date) FROM debate_v2)"
            ).fetchall()
        }
    except sqlite3.OperationalError:
        pass
    for sid, score in deb.items():
        _upsert_factor(conn, sid, today, "F015", float(score))
        count += 1

    # 排名（每个 date + factor_id）
    for dt, fid in conn.execute(
        "SELECT DISTINCT date, factor_id FROM factor_values ORDER BY date"
    ).fetchall():
        vals = conn.execute(
            "SELECT stock_id, value FROM factor_values WHERE factor_id=? AND date=? ORDER BY value DESC",
            (fid, dt),
        ).fetchall()
        for rank, (sid, _) in enumerate(vals, 1):
            conn.execute(
                "UPDATE factor_values SET rank=? WHERE stock_id=? AND date=? AND factor_id=?",
                (rank, sid, dt, fid),
            )

    conn.commit()
    conn.close()
    from services.factor_values_wide import migrate_eav_to_wide

    wide = migrate_eav_to_wide()
    from services.factor_incremental import ensure_log_table

    log_conn = sqlite3.connect(DB_PATH)
    ensure_log_table(log_conn)
    log_conn.execute(
        """INSERT INTO factor_compute_log (mode, target_date, stocks_touched, cells_written)
           VALUES ('full', ?, ?, ?)""",
        (today, len(stocks), count),
    )
    log_conn.commit()
    log_conn.close()
    return {
        "date": today,
        "factors_computed": count,
        "backfill": backfill,
        "s0_setup": s0,
        "wide_rows": wide.get("wide_rows"),
    }


def factor_ic_analysis(factor_id: str, forward_days: int = 20) -> dict:
    """单因子 IC（未来收益）"""
    from services.ic_engine import analyze_factor_id

    return analyze_factor_id(factor_id, forward_days=forward_days)


def factor_layer_backtest(factor_id: str, forward_days: int = 20) -> dict:
    """分层回测：Top/Bottom 20% 未来收益差"""
    from services.ic_engine import factor_layer_forward_returns

    return factor_layer_forward_returns(factor_id, forward_days=forward_days)


def factor_extended_analysis(factor_id: str, forward_days: int = 20) -> dict:
    """IC + 分层 + S1 扩展指标"""
    from services.factor_metrics import analyze_factor_metrics

    ic = factor_ic_analysis(factor_id, forward_days=forward_days)
    layer = factor_layer_backtest(factor_id, forward_days=forward_days)
    metrics = analyze_factor_metrics(factor_id, forward_days=forward_days)
    return {
        "factor_id": factor_id,
        "forward_days": forward_days,
        "ic": ic.get("ic_series", []),
        "mean_ic": ic.get("mean_ic"),
        "mean_rank_ic": ic.get("mean_rank_ic"),
        "ir": ic.get("ir"),
        "ic_positive_ratio": ic.get("ic_positive_ratio"),
        "survivorship_adjusted": ic.get("survivorship_adjusted", True),
        "layer": layer,
        "monotonicity": metrics.get("monotonicity"),
        "turnover": metrics.get("turnover"),
        "ic_significance": metrics.get("ic_significance"),
        "long_short": metrics.get("long_short"),
        "n_cross_sections": metrics.get("n_cross_sections"),
        **({"error": err} if (err := metrics.get("error") or ic.get("error")) else {}),
    }
