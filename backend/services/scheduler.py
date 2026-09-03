"""每日自动任务调度 — 收盘后自动跑全流程"""
import logging
import sqlite3
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from config import DATA_DIR, DB_PATH, latest_trading_date

# 各市场用各自时区触发:A股按北京、美股按美东。裸 datetime.now() 会跟着主机时区
# (如机器在美西 PDT),导致 A股盘后任务错位约15小时——故显式指定时区。
_TZ_CN = ZoneInfo("Asia/Shanghai")
_TZ_US = ZoneInfo("America/New_York")

logger = logging.getLogger(__name__)

# scheduler_state 表的 key —— 记录各触发点最近一次成功执行的日期(YYYY-MM-DD)
_STATE_KEYS = {
    "nightly_fetch": "last_nightly_fetch_date",
    "weekly_sync": "last_weekly_sync_date",
    "technical_retry": "last_technical_retry_date",
    "daily_tasks": "last_daily_tasks_date",
    "morning_tasks": "last_morning_tasks_date",
    "us_daily": "last_us_daily_date",
}


import json
import os

_STATE_FILE = os.path.join(DATA_DIR, "scheduler_state.json")

# ── WAL 看门狗 + 调度子进程登记表 ───────────────────────────────
# 常驻读连接会阻塞被动 checkpoint,WAL 曾在一次卡死的 daily 里涨到 158GB 打爆全站。
# 软顶:超过就主动 TRUNCATE(能收多少收多少)。硬顶:强杀正在跑的调度子进程 + TRUNCATE
# ——止损优先。被杀的那一步未提交事务自动作废(SQLite WAL 语义),已提交步骤全部保留。
_WAL_SOFT_CAP_MB = 512
_WAL_HARD_CAP_MB = 8192
_running_procs: dict = {}          # pid -> {"proc","label","deadline"}
_procs_lock = threading.Lock()


def _wal_mb() -> float:
    try:
        wal_path = DB_PATH + "-wal"
        return os.path.getsize(wal_path) / 1e6 if os.path.exists(wal_path) else 0.0
    except OSError:
        return 0.0


def _kill_proc_tree(proc, label: str) -> bool:
    """强杀子进程及其整个进程组(孙进程 tushare/qlib 也一并带走)。
    子进程以 start_new_session=True 起,自成进程组,os.killpg 才能连根拔。"""
    import signal

    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        return True
    except Exception:
        try:
            proc.kill()  # 退化:至少杀直接子进程
            return True
        except Exception as e:
            logger.warning("[看门狗] 杀 %s 失败: %s", label, e)
            return False


def _kill_running_jobs(reason: str) -> list[str]:
    killed = []
    with _procs_lock:
        for pid, info in list(_running_procs.items()):
            if _kill_proc_tree(info["proc"], info["label"]):
                killed.append(f"{info['label']}(pid={pid})")
    if killed:
        logger.error("[看门狗] %s → 强杀: %s", reason, ", ".join(killed))
    return killed


def _wal_watchdog_loop():
    """独立线程,每 60s 巡检两件事——不受调度主循环在 subprocess 阻塞期间无法自查的影响
    (那正是上次 WAL 涨到 158GB 无人干预的根因):
    ① WAL:软顶主动 TRUNCATE;硬顶强杀在跑作业再 TRUNCATE。
    ② 作业超时:communicate(timeout) 这次实测未能杀掉 6h 的作业,故用独立 deadline 巡检兜底
       ——任何登记作业超过 deadline 就 killpg 整个进程组(可靠,不依赖 communicate 行为)。"""
    import database as _db

    while True:
        time.sleep(60)
        try:
            # ② 超时兜底:先查有没有作业跑过头
            now = time.time()
            overdue = []
            with _procs_lock:
                for pid, info in list(_running_procs.items()):
                    if now > info["deadline"]:
                        overdue.append(info)
            for info in overdue:
                _kill_proc_tree(info["proc"], info["label"])
                logger.error(
                    "[看门狗] 作业 %s 超时(>%ds)→ 强杀进程组",
                    info["label"], info["deadline_sec"],
                )

            # ① WAL 上限
            wal = _wal_mb()
            if wal >= _WAL_HARD_CAP_MB:
                _kill_running_jobs(f"WAL={wal:.0f}MB≥硬顶{_WAL_HARD_CAP_MB}MB")
                time.sleep(2)  # 等写进程真正退出,TRUNCATE 才能拿到无写者的库
                r = _db.checkpoint("TRUNCATE", wal_threshold_mb=0)
                logger.error("[看门狗] 硬顶后 TRUNCATE: %s", r)
            elif wal >= _WAL_SOFT_CAP_MB:
                r = _db.checkpoint("TRUNCATE", wal_threshold_mb=0)
                if not r.get("skipped"):
                    logger.info("[看门狗] 软顶 TRUNCATE (WAL=%.0fMB): %s", wal, r)
        except Exception as e:
            logger.warning("[看门狗] 检查失败: %s", e)


def _write_state_file(state: dict) -> None:
    tmp = _STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, _STATE_FILE)  # 原子替换,避免半截文件


def _load_state() -> dict[str, str]:
    """从 JSON 文件加载触发点执行日期。与主库锁解耦:批任务持锁数分钟也不影响状态读写,
    彻底断掉"状态存不下→任务每次重启重跑→再持锁"的死循环。文件缺失时一次性从旧 DB 表迁移。"""
    try:
        if os.path.isfile(_STATE_FILE):
            with open(_STATE_FILE, encoding="utf-8") as f:
                return {str(k): str(v or "") for k, v in json.load(f).items()}
    except Exception as e:
        logger.warning("[Scheduler] 读状态文件失败(按空继续): %s", e)
    # 迁移:旧 scheduler_state 表 → 文件(仅首次,拿不到锁就算了下次再迁)
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        try:
            rows = conn.execute("SELECT key, value FROM scheduler_state").fetchall()
            st = {k: v or "" for k, v in rows}
            if st:
                _write_state_file(st)
            return st
        finally:
            conn.close()
    except Exception:
        return {}


def _save_state(key: str, value: str) -> None:
    """持久化触发点执行日期到 JSON 文件(不碰 DB→批任务持锁也能落地)；失败只记日志。"""
    try:
        state: dict = {}
        if os.path.isfile(_STATE_FILE):
            try:
                with open(_STATE_FILE, encoding="utf-8") as f:
                    state = json.load(f)
            except Exception:
                state = {}
        state[key] = value
        _write_state_file(state)
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
    # 用 Popen + 登记表(带 deadline),让看门狗线程能在 WAL 失控或作业超时时强杀。
    # start_new_session=True:子进程自成进程组,孙进程(tushare/qlib)才能被 killpg 连根拔。
    proc = subprocess.Popen(
        [sys.executable, script, job],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    with _procs_lock:
        _running_procs[proc.pid] = {
            "proc": proc, "label": job,
            "deadline": time.time() + timeout_sec, "deadline_sec": timeout_sec,
        }
    try:
        # communicate(timeout) 作首道防线;但实测它曾未能杀掉超时作业,
        # 故真正的兜底是看门狗线程按 deadline 巡检 killpg(见 _wal_watchdog_loop)。
        stdout, stderr = proc.communicate(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        _kill_proc_tree(proc, job)
        stdout, stderr = proc.communicate()
        raise RuntimeError(f"timeout after {timeout_sec}s")
    finally:
        with _procs_lock:
            _running_procs.pop(proc.pid, None)
    lines = [ln for ln in (stdout or "").strip().split("\n") if ln.strip()]
    last = lines[-1] if lines else ""
    if proc.returncode != 0:
        # 被看门狗 kill 时 returncode 为负(信号),提示 WAL 失控止损
        raise RuntimeError(f"exit={proc.returncode} stderr={(stderr or '')[-300:]}")
    return last


def start_scheduler(app):
    """在 FastAPI startup 中调用"""

    def _loop():
        # 状态持久化在 DB，进程重启后自动恢复；触发条件为
        # 「已过当日触发时刻 且 今天尚未执行」→ 错过窗口也会在重启后补跑
        state = _load_state()
        # WAL 看门狗独立跑在 _wal_watchdog_loop 线程(见 start_scheduler)。
        # 不放这里:本循环在 _run_job_subprocess 阻塞期间(可数小时)无法自查,
        # 正是上次 WAL 涨到 158GB 无人干预的根因。
        while True:
            now = datetime.now(_TZ_CN)  # A股任务按北京时间触发
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
                now_minutes >= 9 * 60 + 35
                and state.get(_STATE_KEYS["morning_tasks"]) != today_key
            ):
                logger.info("[Scheduler] %s 触发开盘执行任务(子进程)...", datetime.now().strftime("%H:%M"))
                morning_locked = False
                try:
                    out = _run_job_subprocess("morning", timeout_sec=1800)
                    logger.info("[Scheduler] 开盘执行任务(子进程): %s", out)
                except Exception as e:
                    logger.warning("[Scheduler] run_morning_tasks failed: %s", e)
                    # 锁类失败=瞬态(nightly/批任务持写锁)→ 不落 done 标记,下轮(30s)重试直到锁释放。
                    # 否则一次撞锁就被标记完成、当天永不再执行——开盘建仓/调仓永久不落地的元凶。
                    morning_locked = "locked" in str(e).lower()
                if not morning_locked:
                    state[_STATE_KEYS["morning_tasks"]] = today_key
                    _save_state(_STATE_KEYS["morning_tasks"], today_key)
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

            # ── 美股/ETF:按【美东时间】盘后触发(独立于 A股北京时区)──
            # 美股 16:00 ET 收盘,17:30 后 yfinance EOD 就绪;工作日触发,状态用美东日期。
            us_now = datetime.now(_TZ_US)
            us_key = us_now.strftime("%Y-%m-%d")
            if (
                us_now.weekday() < 5
                and us_now.hour * 60 + us_now.minute >= 17 * 60 + 30
                and state.get(_STATE_KEYS["us_daily"]) != us_key
            ):
                logger.info("[Scheduler] %s ET 触发美股同步(子进程)...", us_now.strftime("%H:%M"))
                try:
                    out = _run_job_subprocess("us_daily", timeout_sec=2400)
                    logger.info("[Scheduler] 美股同步(子进程): %s", out)
                except Exception as e:
                    logger.warning("[Scheduler] us_daily failed: %s", e)
                state[_STATE_KEYS["us_daily"]] = us_key
                _save_state(_STATE_KEYS["us_daily"], us_key)
            time.sleep(30)

    t = threading.Thread(target=_loop, daemon=True, name="daily-scheduler")
    t.start()
    wt = threading.Thread(target=_wal_watchdog_loop, daemon=True, name="wal-watchdog")
    wt.start()
    logger.info(
        "[Scheduler] 每日自动任务已启动 (9:35开盘执行, 15:30收盘入队, 状态持久化+错过窗口自动补跑); "
        "WAL看门狗已启动 (软顶%dMB TRUNCATE / 硬顶%dMB 强杀作业)",
        _WAL_SOFT_CAP_MB, _WAL_HARD_CAP_MB,
    )


def run_morning_tasks() -> str:
    """开盘执行待处理自动卖单/调仓。"""
    msg = ""
    try:
        from services.portfolio_svc import execute_pending_orders_at_open

        r = execute_pending_orders_at_open()
        if r.get("executed"):
            msg += f"开盘执行({r['executed']}: 海龟{r.get('turtle_exits', 0)} 调仓{r.get('rebalances', 0)}) "
        elif r.get("reason"):
            msg += f"开盘执行跳过({r['reason']}) "
        else:
            msg += "开盘执行(无待办) "
        if r.get("errors"):
            msg += f"错误{len(r['errors'])} "
    except Exception as e:
        msg += f"开盘执行:{e} "
    logger.info("[Scheduler] %s", msg.strip())
    return msg


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


def _open_daily_log():
    """分步日志落盘 → 子进程被 kill/超时后仍能看到卡在哪一步(stdout 被 capture 吞掉)。"""
    from datetime import date as dt_date

    try:
        log_dir = os.path.join(DATA_DIR, "logs")
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, f"daily_{dt_date.today():%Y%m%d}.log")
        fh = open(path, "a", encoding="utf-8")
        return fh
    except Exception as e:
        logger.warning("[Scheduler] 打开 daily 日志失败: %s", e)
        return None


def _dlog(fh, text: str) -> None:
    """写一行带时间戳的分步日志并立即 flush(卡死时最后一行=正卡住的步)。"""
    if fh is None:
        return
    try:
        fh.write(f"{datetime.now():%H:%M:%S} WAL={_wal_mb():.0f}MB  {text}\n")
        fh.flush()
    except Exception:
        pass


def _bound_wal(fh=None) -> None:
    """重写入步骤之间主动 TRUNCATE,不让 WAL 在一次 daily 里堆到失控(治本,配合看门狗兜底)。"""
    try:
        import database as _db

        r = _db.checkpoint("TRUNCATE", wal_threshold_mb=0)
        _dlog(fh, f"· WAL自限 {r.get('result')}")
    except Exception as e:
        _dlog(fh, f"· WAL自限失败 {e}")


def run_daily_tasks():
    from datetime import date as dt_date

    msg = ""
    _fh = _open_daily_log()
    _dlog(_fh, "════ run_daily_tasks 开始 ════")

    # ① 批量日行情同步 — 主源 Tushare（按日批量，快且不受 eastmoney 封锁影响）
    #    15:30 紧贴收盘,Tushare 当日日线可能尚未发布：未就绪则等待重试(最多4次×15分钟)
    #    失败时回退 tencent/eastmoney 逐股路径
    try:
        import subprocess, sys, os
        scripts_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts")
        for attempt in range(1, 5):
            _dlog(_fh, f"▶ ①行情同步[tushare] 第{attempt}次")
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
                _dlog(_fh, f"· 当日行情未发布,sleep 900s 后重试")
                time.sleep(900)
        last_line = (result.stdout or "").strip().split("\n")[-1]
        msg += f"行情同步[tushare]({last_line[:80]}) "
        _bound_wal(_fh)
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

    # ② 市场页特色数据：沪深股通十大成交 / L2 资金流 / 资金共振 / 市场广度·总貌
    try:
        import subprocess, sys, os

        scripts_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts")
        for script, label, extra_args, timeout in [
            ("tushare_sync_hsgt_top10.py", "沪深股通", ["--days", "5"], 600),
            ("tushare_sync_moneyflow_detail.py", "L2资金流", ["--days", "5"], 1200),
            ("compute_alpha_factors_v1.py", "资金共振", ["--days", "5", "--skip-surprise"], 900),
            ("akshare_sync_market_breadth.py", "市场广度", [], 300),
            ("tushare_sync_etf.py", "ETF", ["--quotes-days", "3", "--flow-days", "3"], 600),
            # 美股/ETF 已拆到「us_daily」按美东时间盘后触发,不再挂在 A股 daily 里。
        ]:
            try:
                _dlog(_fh, f"▶ ②{label}")
                r = subprocess.run(
                    [sys.executable, os.path.join(scripts_dir, script)] + extra_args,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                tail = (r.stdout or "").strip().split("\n")[-1][:50]
                if r.returncode == 0:
                    msg += f"{label}({tail}) "
                else:
                    msg += f"{label}失败({(r.stderr or tail)[-40:]}) "
            except Exception as e:
                msg += f"{label}:{str(e)[:40]} "
        _bound_wal(_fh)
    except Exception as e:
        msg += f"市场特色:{e} "

    # 同花顺当日强势股归因(市场页热点)。此前从未接入调度→ths_hotspots 停更数月。
    try:
        _dlog(_fh, "▶ 同花顺热点")
        from services.astock_data import sync_ths_hotspots

        r = sync_ths_hotspots()
        msg += f"同花顺热点({r.get('error') or r.get('count', 0)}) "
    except Exception as e:
        msg += f"同花顺热点:{str(e)[:40]} "

    # 目标日期必须在行情同步之后解析，否则全流程按旧交易日计算
    today = latest_trading_date()
    msg = f"{dt_date.today()}→{today} 自动任务: " + msg

    try:
        _dlog(_fh, "▶ 估值")
        from services.valuation_engine import compute_valuation_scores

        r = compute_valuation_scores()
        msg += f"估值({r.get('computed', 0)}) "
    except Exception as e:
        msg += f"估值:{e} "

    try:
        _dlog(_fh, "▶ 基本面基准")
        from services.factor_percentile_cache import refresh_daily_baseline

        r = refresh_daily_baseline()
        msg += f"基本面基准({r.get('stock_count', 0)}) "
    except Exception as e:
        msg += f"基本面基准:{e} "

    try:
        _dlog(_fh, "▶ 技术面")
        from services.batch_score_compute import _active_ids, compute_technical

        r = compute_technical(_active_ids(None), today)
        msg += f"技术面(filled={r.get('filled', 0)},cache={r.get('from_cache', 0)}) "
        _bound_wal(_fh)
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

        _dlog(_fh, "▶ 因子")
        if has_log:
            from services.factor_incremental import compute_factors_incremental

            r = compute_factors_incremental()
            msg += f"因子增量({r.get('cells_written', 0)}) "
        else:
            from services.factor_factory import compute_factors

            r = compute_factors()
            msg += f"因子全量({r.get('factors_computed', 0)}) "
        _bound_wal(_fh)
    except Exception as e:
        msg += f"因子:{e} "

    try:
        _dlog(_fh, "▶ 因子历史")
        from services.factor_history_expand import expand_factor_history

        r = expand_factor_history(days=90)
        msg += (
            f"因子历史({r.get('factor_history_days', 0)}天,"
            f"算{r.get('computed_days', '?')}/跳{r.get('skipped_days', '?')}) "
        )
    except Exception as e:
        msg += f"因子历史:{e} "

    try:
        _dlog(_fh, "▶ 因子保留")
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
        _dlog(_fh, "▶ 利息保障")
        from services.financial_backfill import backfill_interest_coverage

        r = backfill_interest_coverage()
        msg += f"利息保障({r.get('updated', 0)}) "
    except Exception as e:
        msg += f"利息保障:{e} "

    try:
        _dlog(_fh, "▶ 因子合成")
        from services.factor_merge_preset import run_preset_merges

        r = run_preset_merges()
        if r.get("error"):
            msg += f"因子合成({r['error']}) "
        else:
            msg += f"因子合成({r.get('success_count', 0)}/{r.get('presets_run', 0)}) "
    except Exception as e:
        msg += f"因子合成:{e} "

    try:
        _dlog(_fh, "▶ 评分历史")
        from services.score_history_expand import expand_score_history

        r = expand_score_history(days=90)
        msg += (
            f"评分历史({r.get('score_history_days', 0)}天,"
            f"算{r.get('computed_days', '?')}/跳{r.get('skipped_days', '?')}) "
        )
    except Exception as e:
        msg += f"评分历史:{e} "

    try:
        _dlog(_fh, "▶ 主力流")
        from services.fund_flow_sync import sync_stock_fund_flow

        ff = sync_stock_fund_flow()
        msg += f"主力流({ff.get('synced', 0)}) "
    except Exception as e:
        msg += f"主力流:{e} "

    try:
        _dlog(_fh, "▶ 资金")
        from services.capital_scorer import compute_all_capital

        results = compute_all_capital(today)
        n = _persist_dimension_rows("capital_scores", results)
        synced = _sync_to_comprehensive("capital_scores", "capital_score", today)
        msg += f"资金({n},同步{synced}) "
        _bound_wal(_fh)
    except Exception as e:
        msg += f"资金:{e} "

    try:
        _dlog(_fh, "▶ 情绪")
        from services.sentiment_scorer import compute_all_sentiment

        results = compute_all_sentiment(today)
        n = _persist_dimension_rows("sentiment_scores", results)
        synced = _sync_to_comprehensive("sentiment_scores", "mood_score", today)
        msg += f"情绪({n},同步{synced}) "
        _bound_wal(_fh)
    except Exception as e:
        msg += f"情绪:{e} "

    try:
        _dlog(_fh, "▶ 政策同步")
        synced = _sync_to_comprehensive("policy_scores", "policy_score", today)
        msg += f"政策同步({synced}) "
    except Exception as e:
        msg += f"政策同步:{e} "

    try:
        _dlog(_fh, "▶ ML训练")
        from config import ML_DAILY_TRAIN

        if not ML_DAILY_TRAIN:
            # 默认跳过:预测消费长期 force-off,每天训练只会跑到 900s 超时白耗(见 config)
            msg += "ML训练:跳过(AFR_ML_DAILY_TRAIN=false) "
        else:
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
        _dlog(_fh, "▶ DB快照")
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
        _dlog(_fh, "▶ 备份")
        import database as db

        db.backup()
        msg += "备份OK "
    except Exception as e:
        msg += f"备份失败:{e} "

    try:
        _dlog(_fh, "▶ V5日常")
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
        _dlog(_fh, "▶ 宏观")
        from services.macro_sync import sync_macro_indicators

        sync_macro_indicators()
        msg += "宏观OK "
    except Exception as e:
        msg += f"宏观:{e} "

    try:
        from services.batch_score_guard import can_run_sync
        from services.comprehensive import sync_all_dimensions
        from services.score_gap_scanner import scan_gaps

        _dlog(_fh, "▶ 维度同步")
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
        _bound_wal(_fh)
    except Exception as e:
        msg += f"维度同步:{e} "

    try:
        _dlog(_fh, "▶ 模拟盘快照/入队")
        from services.portfolio_svc import (
            queue_momentum_crashes,
            queue_scheduled_rebalances,
            queue_turtle_exits,
            snapshot_all_portfolios,
        )

        r = snapshot_all_portfolios()
        msg += f"模拟盘快照({r.get('updated', 0)}) "
        te = queue_turtle_exits()
        if te.get("queued"):
            msg += f"海龟出场入队({te['queued']}→{te.get('execute_date')}) "
        mc = queue_momentum_crashes()
        if mc.get("queued"):
            msg += f"动量崩溃入队({mc['queued']}→{mc.get('execute_date')}) "
        rb = queue_scheduled_rebalances()
        if rb.get("queued"):
            msg += f"调仓入队({rb['queued']}→{rb.get('execute_date')}) "
    except Exception as e:
        msg += f"模拟盘:{e} "

    try:
        _dlog(_fh, "▶ 因子分析预热")
        from services.factor_analysis_cache import warm_all

        r = warm_all(forward_days=20)
        msg += f"因子分析预热({r.get('warmed', 0)}/{r.get('total', 0)}) "
    except Exception as e:
        msg += f"因子分析预热:{e} "

    _dlog(_fh, f"════ run_daily_tasks 结束 ════\n{msg}")
    if _fh is not None:
        try:
            _fh.close()
        except Exception:
            pass
    logger.info("[Scheduler] %s", msg)
    return msg
