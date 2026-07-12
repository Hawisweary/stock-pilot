"""单线程异步任务队列 — 避免 SQLite 写锁与长任务阻塞 API"""
from __future__ import annotations

import json
import sqlite3
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, Optional

import config

JOB_HEARTBEAT_INTERVAL_SEC = 30
JOB_STALE_TIMEOUT_MIN = 10
BATCH_FILL_JOB_TYPE = "batch_score_fill"
DEBATE_BATCH_JOB_TYPE = "debate_batch"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Job:
    id: str
    job_type: str
    payload: dict
    status: JobStatus = JobStatus.PENDING
    result: Optional[dict] = None
    error: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    heartbeat_at: Optional[str] = None
    stale_timeout_min: int = JOB_STALE_TIMEOUT_MIN


_HANDLERS: Dict[str, Callable[[dict], dict]] = {}
_lock = threading.Lock()
_queue: list[str] = []
_jobs: Dict[str, Job] = {}
_worker_started = False


def register_handler(job_type: str, fn: Callable[[dict], dict]) -> None:
    _HANDLERS[job_type] = fn


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS job_runs (
            id TEXT PRIMARY KEY,
            job_type TEXT NOT NULL,
            payload_json TEXT,
            status TEXT NOT NULL,
            result_json TEXT,
            error TEXT,
            created_at TEXT,
            started_at TEXT,
            finished_at TEXT,
            heartbeat_at TEXT,
            stale_timeout_min INTEGER DEFAULT 10
        )"""
    )
    cols = {r[1] for r in conn.execute("PRAGMA table_info(job_runs)").fetchall()}
    if "heartbeat_at" not in cols:
        conn.execute("ALTER TABLE job_runs ADD COLUMN heartbeat_at TEXT")
    if "stale_timeout_min" not in cols:
        conn.execute("ALTER TABLE job_runs ADD COLUMN stale_timeout_min INTEGER DEFAULT 10")
    conn.commit()


def _job_from_row(row: sqlite3.Row) -> Job:
    payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
    result = json.loads(row["result_json"]) if row["result_json"] else None
    return Job(
        id=row["id"],
        job_type=row["job_type"],
        payload=payload,
        status=JobStatus(row["status"]),
        result=result,
        error=row["error"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        heartbeat_at=row["heartbeat_at"] if "heartbeat_at" in row.keys() else None,
        stale_timeout_min=int(row["stale_timeout_min"] or JOB_STALE_TIMEOUT_MIN)
        if "stale_timeout_min" in row.keys()
        else JOB_STALE_TIMEOUT_MIN,
    )


def _persist(job: Job) -> None:
    try:
        conn = sqlite3.connect(config.DB_PATH, timeout=120)
        conn.row_factory = sqlite3.Row
        _ensure_table(conn)
        conn.execute(
            """INSERT OR REPLACE INTO job_runs
               (id, job_type, payload_json, status, result_json, error, created_at,
                started_at, finished_at, heartbeat_at, stale_timeout_min)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                job.id,
                job.job_type,
                json.dumps(job.payload, ensure_ascii=False),
                job.status.value,
                json.dumps(job.result, ensure_ascii=False) if job.result else None,
                job.error,
                job.created_at,
                job.started_at,
                job.finished_at,
                job.heartbeat_at,
                job.stale_timeout_min,
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def touch_job_heartbeat(job_id: str) -> None:
    now = datetime.now().isoformat()
    with _lock:
        job = _jobs.get(job_id)
        if job:
            job.heartbeat_at = now
    _persist_job_field(job_id, heartbeat_at=now)


def update_job_progress(job_id: str, partial_result: dict) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job:
            job.result = partial_result
            job.heartbeat_at = datetime.now().isoformat()
    _persist_job_field(
        job_id,
        result_json=json.dumps(partial_result, ensure_ascii=False),
        heartbeat_at=datetime.now().isoformat(),
    )


def _persist_job_field(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    try:
        conn = sqlite3.connect(config.DB_PATH, timeout=120)
        _ensure_table(conn)
        set_clause = ", ".join(f"{k}=?" for k in fields)
        conn.execute(
            f"UPDATE job_runs SET {set_clause} WHERE id=?",
            (*fields.values(), job_id),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _is_stale(job: Job) -> bool:
    if job.status != JobStatus.RUNNING:
        return False
    ref = job.heartbeat_at or job.started_at or job.created_at
    if not ref:
        return False
    try:
        dt = datetime.fromisoformat(ref)
    except ValueError:
        return False
    return datetime.now() - dt > timedelta(minutes=job.stale_timeout_min)


def _load_job_from_db(job_id: str) -> Optional[Job]:
    try:
        conn = sqlite3.connect(config.DB_PATH, timeout=120)
        conn.row_factory = sqlite3.Row
        _ensure_table(conn)
        row = conn.execute("SELECT * FROM job_runs WHERE id=?", (job_id,)).fetchone()
        conn.close()
        if not row:
            return None
        return _job_from_row(row)
    except Exception:
        return None


def _find_active_job_from_db(job_type: str) -> Optional[Job]:
    try:
        conn = sqlite3.connect(config.DB_PATH, timeout=120)
        conn.row_factory = sqlite3.Row
        _ensure_table(conn)
        rows = conn.execute(
            """
            SELECT * FROM job_runs
            WHERE job_type=? AND status IN ('pending', 'running')
            ORDER BY created_at DESC LIMIT 1
            """,
            (job_type,),
        ).fetchall()
        conn.close()
        for row in rows:
            job = _job_from_row(row)
            if _is_stale(job):
                fail_job(job.id, "stale timeout: no heartbeat")
                continue
            return job
    except Exception:
        pass
    return None


def _find_active_batch_fill_from_db() -> Optional[Job]:
    return _find_active_job_from_db(BATCH_FILL_JOB_TYPE)


def _find_active_debate_batch_from_db() -> Optional[Job]:
    return _find_active_job_from_db(DEBATE_BATCH_JOB_TYPE)


def cleanup_stale_batch_jobs() -> int:
    """启动时清理中断/超时的 batch_score_fill job。"""
    count = 0
    try:
        conn = sqlite3.connect(config.DB_PATH, timeout=120)
        conn.row_factory = sqlite3.Row
        _ensure_table(conn)
        rows = conn.execute(
            """
            SELECT * FROM job_runs
            WHERE job_type=? AND status IN ('pending', 'running')
            """,
            (BATCH_FILL_JOB_TYPE,),
        ).fetchall()
        conn.close()
        for row in rows:
            job = _job_from_row(row)
            if _is_stale(job) or job.status in (JobStatus.PENDING, JobStatus.RUNNING):
                fail_job(job.id, "interrupted: stale or worker restarted")
                count += 1
    except Exception:
        pass
    return count


def find_active_batch_fill() -> Optional[Job]:
    with _lock:
        for job in _jobs.values():
            if job.job_type == BATCH_FILL_JOB_TYPE and job.status in (
                JobStatus.PENDING,
                JobStatus.RUNNING,
            ):
                if _is_stale(job):
                    fail_job(job.id, "stale timeout: no heartbeat")
                    continue
                return job
    return _find_active_batch_fill_from_db()


def find_active_debate_batch() -> Optional[Job]:
    with _lock:
        for job in _jobs.values():
            if job.job_type == DEBATE_BATCH_JOB_TYPE and job.status in (
                JobStatus.PENDING,
                JobStatus.RUNNING,
            ):
                if _is_stale(job):
                    fail_job(job.id, "stale timeout: no heartbeat")
                    continue
                return job
    return _find_active_debate_batch_from_db()


def can_enqueue_batch_fill() -> tuple[bool, str | None, str | None]:
    active = find_active_batch_fill()
    if active:
        return False, "已有补算任务运行中", active.id
    debate = find_active_debate_batch()
    if debate:
        return False, "辩论批量任务运行中", debate.id
    return True, None, None


def can_enqueue_debate_batch() -> tuple[bool, str | None, str | None]:
    active = find_active_debate_batch()
    if active:
        return False, "已有辩论批量任务运行中", active.id
    fill = find_active_batch_fill()
    if fill:
        return False, "维度补算任务运行中", fill.id
    return True, None, None


def fail_job(job_id: str, reason: str) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            job = _load_job_from_db(job_id)
        if not job:
            return
        job.status = JobStatus.FAILED
        job.error = reason
        job.finished_at = datetime.now().isoformat()
        _jobs[job_id] = job
    _persist(_jobs.get(job_id) or job)


def cancel_job(job_id: str) -> bool:
    with _lock:
        job = _jobs.get(job_id) or _load_job_from_db(job_id)
        if not job:
            return False
        if job.status not in (JobStatus.PENDING, JobStatus.RUNNING):
            return False
        job.status = JobStatus.CANCELLED
        job.error = "cancelled by admin"
        job.finished_at = datetime.now().isoformat()
        _jobs[job_id] = job
    _persist(_jobs[job_id])
    return True


def _run_with_heartbeat(job_id: str, fn: Callable[[], dict]) -> dict:
    stop = threading.Event()

    def _beat() -> None:
        while not stop.wait(JOB_HEARTBEAT_INTERVAL_SEC):
            touch_job_heartbeat(job_id)

    touch_job_heartbeat(job_id)
    t = threading.Thread(target=_beat, daemon=True, name=f"heartbeat-{job_id}")
    t.start()
    try:
        return fn()
    finally:
        stop.set()


def enqueue(
    job_type: str,
    payload: Optional[dict] = None,
    *,
    job_id: str | None = None,
) -> Job:
    """入队并返回 Job（单 worker 串行执行）"""
    global _worker_started
    jid = job_id or str(uuid.uuid4())[:12]
    job = Job(id=jid, job_type=job_type, payload=payload or {})
    with _lock:
        _jobs[job.id] = job
        _queue.append(job.id)
    _persist(job)
    if not _worker_started:
        _worker_started = True
        t = threading.Thread(target=_worker_loop, daemon=True, name="afr-job-queue")
        t.start()
    return job


def enqueue_batch_fill(payload: dict) -> Job:
    ok, reason, _ = can_enqueue_batch_fill()
    if not ok:
        raise RuntimeError(reason or "batch fill busy")
    calc_date = payload.get("target_date") or config.latest_trading_date()
    suffix = str(uuid.uuid4())[:4]
    job_id = f"bf-{calc_date.replace('-', '')}-{suffix}"
    full_payload = {**payload, "job_id": job_id}
    return enqueue(BATCH_FILL_JOB_TYPE, full_payload, job_id=job_id)


def enqueue_debate_batch(payload: dict) -> Job:
    ok, reason, _ = can_enqueue_debate_batch()
    if not ok:
        raise RuntimeError(reason or "debate batch busy")
    today = datetime.now().strftime("%Y%m%d")
    suffix = str(uuid.uuid4())[:4]
    job_id = f"db-{today}-{suffix}"
    full_payload = {**payload, "job_id": job_id}
    return enqueue(DEBATE_BATCH_JOB_TYPE, full_payload, job_id=job_id)


def get_job(job_id: str) -> Optional[Job]:
    with _lock:
        job = _jobs.get(job_id)
    if job:
        return job
    job = _load_job_from_db(job_id)
    if job:
        with _lock:
            _jobs[job_id] = job
    return job


def list_jobs(limit: int = 20) -> list[Job]:
    with _lock:
        items = sorted(_jobs.values(), key=lambda j: j.created_at, reverse=True)
    return items[:limit]


def _worker_loop() -> None:
    while True:
        job_id = None
        with _lock:
            if _queue:
                job_id = _queue.pop(0)
        if not job_id:
            threading.Event().wait(0.5)
            continue
        job = get_job(job_id)
        if not job or job.status == JobStatus.CANCELLED:
            continue
        handler = _HANDLERS.get(job.job_type)
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now().isoformat()
        job.heartbeat_at = job.started_at
        with _lock:
            _jobs[job.id] = job
        _persist(job)
        try:
            if not handler:
                raise RuntimeError(f"未知任务类型: {job.job_type}")
            if job.job_type in (BATCH_FILL_JOB_TYPE, DEBATE_BATCH_JOB_TYPE):
                job.result = _run_with_heartbeat(job.id, lambda: handler(job.payload))
            else:
                job.result = handler(job.payload)
            job.status = JobStatus.DONE
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error = str(e)
            job.result = {"traceback": traceback.format_exc()[-500:]}
        job.finished_at = datetime.now().isoformat()
        with _lock:
            _jobs[job.id] = job
        for attempt in range(5):
            try:
                _persist(job)
                break
            except Exception:
                if attempt == 4:
                    pass
                else:
                    threading.Event().wait(0.3 * (attempt + 1))


def _register_builtin_handlers() -> None:
    def _factor_compute(_payload: dict) -> dict:
        if _payload.get("incremental"):
            from services.factor_incremental import compute_factors_incremental

            return compute_factors_incremental()
        from services.factor_factory import compute_factors

        return compute_factors(backfill=_payload.get("backfill", True))

    def _qlib_train(payload: dict) -> dict:
        from services.ml_predictions import run_qlib_train_job

        return run_qlib_train_job(payload)

    def _factor_expand(payload: dict) -> dict:
        from services.factor_history_expand import expand_factor_history

        return expand_factor_history(days=int(payload.get("days", 90)))

    def _interest_backfill(_payload: dict) -> dict:
        from services.financial_backfill import backfill_interest_coverage

        return backfill_interest_coverage()

    def _factor_merge(_payload: dict) -> dict:
        from services.factor_merge_preset import run_preset_merges

        return run_preset_merges(skip_ic_check=_payload.get("skip_ic_check", False))

    def _score_expand(payload: dict) -> dict:
        from services.score_history_expand import expand_score_history

        return expand_score_history(days=int(payload.get("days", 90)))

    def _ml_sync(_payload: dict) -> dict:
        from services.ml_predictions import sync_ml_to_comprehensive

        return sync_ml_to_comprehensive(ml_weight=float(_payload.get("ml_weight", 0.08)))

    def _batch_score_fill(payload: dict) -> dict:
        import database as db

        if not db.is_initialized():
            db.init()
        from services.batch_score_orchestrator import fill_gaps

        result = fill_gaps(
            mode=payload.get("mode", "sync_only"),
            dimensions=payload.get("dimensions"),
            stock_ids=payload.get("stock_ids"),
            target_date=payload.get("target_date"),
            skip_no_source=payload.get("skip_no_source", True),
            job_id=payload.get("job_id"),
            triggered_by=payload.get("triggered_by", "api"),
        )

        # Path B — batch 末尾统一触发一次 V5 重算
        if config.V5_RECALC_AT_BATCH_END:
            try:
                from services.v5_scorer import compute_all_v5_scores
                changed_ids = payload.get("stock_ids") or None
                v5_result = compute_all_v5_scores(stock_ids=changed_ids)
                result["v5_recalc"] = {
                    "computed": v5_result.get("computed", 0),
                    "skipped_hard_fail": False,
                }
                print(
                    f"[job_queue] Path B V5 batch done: computed={v5_result.get('computed')} "
                    f"ids={'all' if changed_ids is None else len(changed_ids)}"
                )
            except Exception as e:
                print(f"[job_queue] Path B V5 batch failed: {e}")
                result["v5_recalc"] = {"computed": 0, "skipped_hard_fail": True}

        return result

    def _debate_batch(payload: dict) -> dict:
        # v3.0: 辩论链路已移除；此 handler 保留注册以消化历史遗留任务，直接返回 gone。
        return {"status": "gone", "message": "debate_batch removed in v3.0; use composite_v5"}

    def _stock_onboard(payload: dict) -> dict:
        from services.onboard_service import run_onboard_job

        return run_onboard_job(payload)

    def _factor_gp(payload: dict) -> dict:
        from services.factor_gp import run_gp_search

        return run_gp_search(
            population=int(payload.get("population", 12)),
            generations=int(payload.get("generations", 8)),
            forward_days=int(payload.get("forward_days", 20)),
            top_k=int(payload.get("top_k", 3)),
        )

    register_handler("factor_compute", _factor_compute)
    register_handler("factor_gp", _factor_gp)
    register_handler("qlib_train", _qlib_train)
    register_handler("factor_expand", _factor_expand)
    register_handler("interest_backfill", _interest_backfill)
    register_handler("factor_merge", _factor_merge)
    register_handler("score_expand", _score_expand)
    register_handler("ml_sync", _ml_sync)
    register_handler(BATCH_FILL_JOB_TYPE, _batch_score_fill)
    register_handler(DEBATE_BATCH_JOB_TYPE, _debate_batch)
    from services.onboard_service import ONBOARD_JOB_TYPE

    register_handler(ONBOARD_JOB_TYPE, _stock_onboard)


_register_builtin_handlers()
