"""每日自动任务调度 — 收盘后自动跑全流程"""
import logging
import sqlite3
import threading
import time
from datetime import datetime

from config import DB_PATH, latest_trading_date

logger = logging.getLogger(__name__)

# scheduler_state 表的 key —— 记录各触发点最近一次成功执行的日期(YYYY-MM-DD)
_STATE_KEYS = {
    "nightly_fetch": "last_nightly_fetch_date",
    "weekly_sync": "last_weekly_sync_date",
    "technical_retry": "last_technical_retry_date",
    "daily_tasks": "last_daily_tasks_date",
}


def _ensure_state_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS scheduler_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT (datetime('now', 'localtime'))
        )"""
    )


def _load_state() -> dict[str, str]:
    """启动时从 DB 加载各触发点的最近执行日期；表不存在则创建。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        _ensure_state_table(conn)
        conn.commit()
        rows = conn.execute("SELECT key, value FROM scheduler_state").fetchall()
        return {k: v or "" for k, v in rows}
    except Exception as e:
        logger.warning("[Scheduler] 加载调度状态失败(按空状态继续): %s", e)
        return {}
    finally:
        conn.close()


def _save_state(key: str, value: str) -> None:
    """持久化触发点执行日期；失败只记日志，不影响调度线程。"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        try:
            _ensure_state_table(conn)
            conn.execute(
                """INSERT INTO scheduler_state (key, value, updated_at)
                   VALUES (?, ?, datetime('now', 'localtime'))
                   ON CONFLICT(key) DO UPDATE SET
                     value=excluded.value, updated_at=excluded.updated_at""",
                (key, value),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning("[Scheduler] 保存调度状态失败 %s=%s: %s", key, value, e)


def _weekly_overdue_days(last_date: str | None, today_key: str) -> int:
    """距上次成功周度同步的天数(用于错过整个触发日后的补跑判断)。"""
    if not last_date:
        return 999
    try:
        from datetime import date as _d

        a = _d.fromisoformat(last_date)
        b = _d.fromisoformat(today_key)
        return (b - a).days
    except Exception:
        return 0


def _run_job_subprocess(job: str, timeout_sec: int) -> str:
    """在独立子进程运行调度作业，避免重任务阻塞 API 进程线程池。

    返回子进程最后一行输出(JSON 摘要)；非零退出码抛异常。
    """
    import os
    import subprocess
    import sys

    script = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "scripts",
        "run_scheduled_job.py",
    )
    r = subprocess.run(
        [sys.executable, script, job],
        capture_output=True,
        text=True,
        timeout=timeout_sec,
    )
    lines = [ln for ln in (r.stdout or "").strip().split("\n") if ln.strip()]
    last = lines[-1] if lines else ""
    if r.returncode != 0:
        raise RuntimeError(f"exit={r.returncode} stderr={(r.stderr or '')[-300:]}")
    return last


def start_scheduler(app):
    """在 FastAPI startup 中调用"""

    def _loop():
        # 状态持久化在 DB，进程重启后自动恢复；触发条件为
        # 「已过当日触发时刻 且 今天尚未执行」→ 错过窗口也会在重启后补跑
        state = _load_state()
        last_wal_check = 0.0
        while True:
            now = datetime.now()

            # WAL 看门狗：每 ~5 分钟检查一次,WAL 超过 512MB 就 TRUNCATE。
            # 常驻读连接会阻塞被动 autocheckpoint,此主动兜底防止 WAL 膨胀
            # 到打爆后端(曾涨到 154GB 导致每个读查询扫穿 WAL 卡死全站)
            if time.time() - last_wal_check > 300:
                last_wal_check = time.time()
                try:
                    import database as _db

                    r = _db.checkpoint("TRUNCATE", wal_threshold_mb=512)
                    if not r.get("skipped"):
                        logger.info("[Scheduler] WAL checkpoint: %s", r)
                except Exception as e:
                    logger.warning("[Scheduler] WAL checkpoint failed: %s", e)

            today_key = now.strftime("%Y-%m-%d")
            from config import (
                V5_NIGHTLY_FETCH_HOUR,
                V5_NIGHTLY_FETCH_MINUTE,
                V5_WEEKLY_HOUR,
                V5_WEEKLY_MINUTE,
                V5_WEEKLY_WEEKDAY,
            )

            now_minutes = now.hour * 60 + now.minute

            # 触发后先执行、执行完(含内部报错)再持久化标记：
            # 中途被 kill → 状态未写入 → 重启后自动补跑（各步骤写库均幂等）
            if (
                now_minutes >= V5_NIGHTLY_FETCH_HOUR * 60 + V5_NIGHTLY_FETCH_MINUTE
                and state.get(_STATE_KEYS["nightly_fetch"]) != today_key
            ):
                try:
                    out = _run_job_subprocess("nightly", timeout_sec=3 * 3600)
                    logger.info("[Scheduler] V5 nightly fetch(子进程): %s", out)
                except Exception as e:
                    logger.warning("[Scheduler] V5 nightly fetch failed: %s", e)
                state[_STATE_KEYS["nightly_fetch"]] = today_key
                _save_state(_STATE_KEYS["nightly_fetch"], today_key)
            if (
                (
                    # 正常:周度触发日且已过触发时刻
                    (now.weekday() == V5_WEEKLY_WEEKDAY
                     and now_minutes >= V5_WEEKLY_HOUR * 60 + V5_WEEKLY_MINUTE)
                    # 补跑:整个触发日后端离线错过时,距上次成功≥7天则任意日补跑
                    # (否则要再等一整周;日/夜间任务无此限制,周度曾因此卡9天)
                    or _weekly_overdue_days(state.get(_STATE_KEYS["weekly_sync"]), today_key) >= 7
                )
                and now_minutes >= V5_WEEKLY_HOUR * 60 + V5_WEEKLY_MINUTE
                and state.get(_STATE_KEYS["weekly_sync"]) != today_key
            ):
                try:
                    out = _run_job_subprocess("weekly", timeout_sec=6 * 3600)
                    logger.info("[Scheduler] V5 weekly sync(子进程): %s", out)
                    # 每周同步申万行业分类
                    import subprocess, sys, os
                    script = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "sync_industry_batch.py")
                    subprocess.Popen([sys.executable, script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception as e:
                    logger.warning("[Scheduler] V5 weekly sync failed: %s", e)
                state[_STATE_KEYS["weekly_sync"]] = today_key
                _save_state(_STATE_KEYS["weekly_sync"], today_key)
            if (
                now.hour >= 8
                and state.get(_STATE_KEYS["technical_retry"]) != today_key
            ):
                try:
                    out = _run_job_subprocess("technical_retry", timeout_sec=3600)
                    logger.info("[Scheduler] technical no_source retry(子进程): %s", out)
                except Exception as e:
                    logger.warning("[Scheduler] technical retry failed: %s", e)
                state[_STATE_KEYS["technical_retry"]] = today_key
                _save_state(_STATE_KEYS["technical_retry"], today_key)
            if (
                now_minutes >= 15 * 60 + 30
                and state.get(_STATE_KEYS["daily_tasks"]) != today_key
            ):
                logger.info("[Scheduler] %s 触发每日任务(子进程)...", datetime.now().strftime("%H:%M"))
                try:
                    out = _run_job_subprocess("daily", timeout_sec=3 * 3600)
                    logger.info("[Scheduler] 每日任务(子进程): %s", out)
                except Exception as e:
                    logger.warning("[Scheduler] run_daily_tasks failed: %s", e)
                state[_STATE_KEYS["daily_tasks"]] = today_key
                _save_state(_STATE_KEYS["daily_tasks"], today_key)
            time.sleep(30)

    t = threading.Thread(target=_loop, daemon=True, name="daily-scheduler")
    t.start()
    logger.info("[Scheduler] 每日自动任务已启动 (15:30触发, 状态持久化+错过窗口自动补跑)")


def _persist_dimension_rows(table: str, rows: list[dict], date_key: str = "date") -> int:
    """写入 capital_scores / sentiment_scores 等维度表。"""
    if not rows:
        return 0
    conn = sqlite3.connect(DB_PATH)
    count = 0
    try:
        if table == "capital_scores":
            for r in rows:
                conn.execute(
                    """INSERT OR REPLACE INTO capital_scores
                    (stock_id, date, composite_score, flow_score, turnover_score,
                     volume_score, change_score, holder_score, breakdown_json)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        r["stock_id"],
                        r.get(date_key, r.get("date")),
                        r.get("composite_score", r.get("score", 0)),
                        r.get("flow_score", 0),
                        r.get("turn_score", r.get("turnover_score", 0)),
                        r.get("volume_score", 0),
                        r.get("change_score", 0),
                        r.get("holder_score", 0),
                        r.get("breakdown_json", "{}"),
                    ),
                )
                count += 1
        elif table == "sentiment_scores":
            for r in rows:
                conn.execute(
                    """INSERT OR REPLACE INTO sentiment_scores
                    (stock_id, date, composite_score, breakdown_json)
                    VALUES (?,?,?,?)""",
                    (
                        r["stock_id"],
                        r.get(date_key, r.get("date")),
                        r.get("score", r.get("composite_score", 0)),
                        r.get("breakdown_json", "{}"),
                    ),
                )
                count += 1
        conn.commit()
    finally:
        conn.close()
    return count


def _sync_to_comprehensive(table: str, column: str, calc_date: str) -> int:
    from services.comprehensive_store import upsert_dimension_score

    conn = sqlite3.connect(DB_PATH)
    synced = 0
    try:
        rows = conn.execute(
            f"SELECT stock_id, composite_score FROM [{table}] WHERE date=? AND composite_score IS NOT NULL",
            (calc_date,),
        ).fetchall()
    finally:
        conn.close()
    for stock_id, score in rows:
        upsert_dimension_score(int(stock_id), column, float(score), calc_date=calc_date)
        synced += 1
    return synced


def _todays_quotes_ready() -> bool:
    """今天(若为交易日)的行情是否已入库。非交易日恒为True。"""
    import sqlite3
    from datetime import date as dt_date

    today_str = dt_date.today().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        cal = conn.execute(
            "SELECT is_open FROM trade_calendar WHERE cal_date=?", (today_str,)
        ).fetchone()
        if cal is not None and not cal[0]:
            return True  # 非交易日
        n = conn.execute(
            "SELECT COUNT(*) FROM stock_daily_quotes WHERE trade_date=?", (today_str,)
        ).fetchone()[0]
        return n > 1000
    except sqlite3.OperationalError:
        return True  # 表缺失等异常时不阻塞流程
    finally:
        conn.close()


def run_daily_tasks():
    from datetime import date as dt_date

    msg = ""

    # ① 批量日行情同步 — 主源 Tushare（按日批量，快且不受 eastmoney 封锁影响）
    #    15:30 紧贴收盘,Tushare 当日日线可能尚未发布：未就绪则等待重试(最多4次×15分钟)
    #    失败时回退 tencent/eastmoney 逐股路径
    try:
        import subprocess, sys, os
        scripts_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts")
        for attempt in range(1, 5):
            result = subprocess.run(
                [sys.executable, os.path.join(scripts_dir, "tushare_bulk_backfill.py"),
                 "--quotes-days", "3", "--skip-financials", "--fund-flow-days", "3"],
                capture_output=True, text=True, timeout=1200,
            )
            if result.returncode != 0:
                raise RuntimeError(f"tushare exit={result.returncode} {(result.stderr or '')[-120:]}")
            if _todays_quotes_ready():
                break
            if attempt < 4:
                msg += f"当日行情未发布(第{attempt}次),15分钟后重试 "
                time.sleep(900)
        last_line = (result.stdout or "").strip().split("\n")[-1]
        msg += f"行情同步[tushare]({last_line[:80]}) "
    except Exception as e:
        msg += f"行情tushare失败:{str(e)[:80]} "
        try:
            result = subprocess.run(
                [sys.executable, os.path.join(scripts_dir, "sync_quotes_batch.py"),
                 "--days", "5", "--workers", "12"],
                capture_output=True, text=True, timeout=900,
            )
            last_line = (result.stdout or "").strip().split("\n")[-1]
            msg += f"行情同步[fallback]({last_line[:60]}) "
        except Exception as e2:
            msg += f"行情fallback:{str(e2)[:60]} "

    # 目标日期必须在行情同步之后解析，否则全流程按旧交易日计算
    today = latest_trading_date()
    msg = f"{dt_date.today()}→{today} 自动任务: " + msg

    try:
        from services.valuation_engine import compute_valuation_scores

        r = compute_valuation_scores()
        msg += f"估值({r.get('computed', 0)}) "
    except Exception as e:
        msg += f"估值:{e} "

    try:
        from services.factor_percentile_cache import refresh_daily_baseline

        r = refresh_daily_baseline()
        msg += f"基本面基准({r.get('stock_count', 0)}) "
    except Exception as e:
        msg += f"基本面基准:{e} "

    try:
        from services.batch_score_compute import _active_ids, compute_technical

        r = compute_technical(_active_ids(None), today)
        msg += f"技术面(filled={r.get('filled', 0)},cache={r.get('from_cache', 0)}) "
    except Exception as e:
        msg += f"技术面:{e} "

    try:
        import sqlite3

        from config import DB_PATH

        has_log = False
        try:
            conn = sqlite3.connect(DB_PATH)
            row = conn.execute(
                "SELECT 1 FROM factor_compute_log LIMIT 1"
            ).fetchone()
            has_log = row is not None
            conn.close()
        except sqlite3.OperationalError:
            has_log = False

        if has_log:
            from services.factor_incremental import compute_factors_incremental

            r = compute_factors_incremental()
            msg += f"因子增量({r.get('cells_written', 0)}) "
        else:
            from services.factor_factory import compute_factors

            r = compute_factors()
            msg += f"因子全量({r.get('factors_computed', 0)}) "
    except Exception as e:
        msg += f"因子:{e} "

    try:
        from services.factor_history_expand import expand_factor_history

        r = expand_factor_history(days=90)
        msg += f"因子历史({r.get('factor_history_days', 0)}天) "
    except Exception as e:
        msg += f"因子历史:{e} "

    try:
        from services.factor_retention import prune_empty_factors, prune_factor_values

        r = prune_factor_values()
        if r.get("pruned"):
            msg += f"因子保留(清{r.get('days_removed')}天/{r['pruned']}行) "
        e0 = prune_empty_factors()
        if e0.get("removed"):
            msg += f"空壳清理({e0['removed']}) "
    except Exception as e:
        msg += f"因子保留:{e} "

    try:
        from services.financial_backfill import backfill_interest_coverage

        r = backfill_interest_coverage()
        msg += f"利息保障({r.get('updated', 0)}) "
    except Exception as e:
        msg += f"利息保障:{e} "

    try:
        from services.factor_merge_preset import run_preset_merges

        r = run_preset_merges()
        if r.get("error"):
            msg += f"因子合成({r['error']}) "
        else:
            msg += f"因子合成({r.get('success_count', 0)}/{r.get('presets_run', 0)}) "
    except Exception as e:
        msg += f"因子合成:{e} "

    try:
        from services.score_history_expand import expand_score_history

        r = expand_score_history(days=90)
        msg += f"评分历史({r.get('score_history_days', 0)}天) "
    except Exception as e:
        msg += f"评分历史:{e} "

    try:
        from services.fund_flow_sync import sync_stock_fund_flow

        ff = sync_stock_fund_flow()
        msg += f"主力流({ff.get('synced', 0)}) "
    except Exception as e:
        msg += f"主力流:{e} "

    try:
        from services.capital_scorer import compute_all_capital

        results = compute_all_capital(today)
        n = _persist_dimension_rows("capital_scores", results)
        synced = _sync_to_comprehensive("capital_scores", "capital_score", today)
        msg += f"资金({n},同步{synced}) "
    except Exception as e:
        msg += f"资金:{e} "

    try:
        from services.sentiment_scorer import compute_all_sentiment

        results = compute_all_sentiment(today)
        n = _persist_dimension_rows("sentiment_scores", results)
        synced = _sync_to_comprehensive("sentiment_scores", "mood_score", today)
        msg += f"情绪({n},同步{synced}) "
    except Exception as e:
        msg += f"情绪:{e} "

    try:
        synced = _sync_to_comprehensive("policy_scores", "policy_score", today)
        msg += f"政策同步({synced}) "
    except Exception as e:
        msg += f"政策同步:{e} "

    try:
        from services.ml_predictions import run_qlib_train_job, sync_ml_to_comprehensive

        tr = run_qlib_train_job({"train_days": 120})
        if tr.get("status") == "done":
            sy = sync_ml_to_comprehensive()
            n_models = len(tr.get("models") or [])
            msg += f"ML(h{tr.get('horizons',[])},{n_models}模型,{sy.get('updated', 0)}股) "
        else:
            msg += f"ML:{tr.get('reason', tr.get('status'))} "
    except Exception as e:
        msg += f"ML:{e} "

    try:
        import os
        import subprocess

        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        snap = os.path.join(root, "scripts", "db_snapshot.sh")
        if os.path.isfile(snap):
            subprocess.run(["bash", snap], cwd=root, capture_output=True, timeout=60, check=False)
            msg += "DB快照OK "
    except Exception as e:
        msg += f"DB快照:{e} "

    try:
        import database as db

        db.backup()
        msg += "备份OK "
    except Exception as e:
        msg += f"备份失败:{e} "

    try:
        from services.v5_data_sync import sync_v5_daily

        v5 = sync_v5_daily()
        cov = v5.get("news_event_coverage") or {}
        msg += (
            f"V5日常({cov.get('coverage_pct', '?')}%,"
            f"{'OK' if v5.get('ok') else '部分'}) "
        )
    except Exception as e:
        msg += f"V5日常:{e} "

    try:
        from services.macro_sync import sync_macro_indicators

        sync_macro_indicators()
        msg += "宏观OK "
    except Exception as e:
        msg += f"宏观:{e} "

    try:
        from services.batch_score_guard import can_run_sync
        from services.comprehensive import sync_all_dimensions
        from services.score_gap_scanner import scan_gaps

        ok, reason = can_run_sync()
        if not ok:
            logger.info("[Scheduler] skip sync_all_dimensions: %s", reason)
            msg += f"维度同步跳过({reason}) "
        else:
            gaps = scan_gaps(target_date=today)
            if gaps.get("missing_total", 0) > 0 or gaps.get("stale_total", 0) > 0:
                sync_result = sync_all_dimensions(
                    stock_ids=None,
                    calc_date=today,
                    overwrite=False,
                    refresh_stale=True,
                )
                msg += f"维度同步({sync_result.get('affected_stocks', 0)}股) "
            else:
                msg += "维度同步(无缺口) "
    except Exception as e:
        msg += f"维度同步:{e} "

    try:
        from services.portfolio_svc import (
            run_scheduled_rebalances,
            run_turtle_exits,
            snapshot_all_portfolios,
        )

        r = snapshot_all_portfolios()
        msg += f"模拟盘快照({r.get('updated', 0)}) "
        te = run_turtle_exits()
        if te.get("exited"):
            msg += f"海龟出场({te['exited']}) "
        rb = run_scheduled_rebalances()
        if rb.get("rebalanced"):
            msg += f"调仓({rb['rebalanced']}) "
    except Exception as e:
        msg += f"模拟盘:{e} "

    try:
        from services.factor_analysis_cache import warm_all

        r = warm_all(forward_days=20)
        msg += f"因子分析预热({r.get('warmed', 0)}/{r.get('total', 0)}) "
    except Exception as e:
        msg += f"因子分析预热:{e} "

    logger.info("[Scheduler] %s", msg)
    return msg
