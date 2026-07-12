"""辩论批量 Phase 1 单元测试"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def debate_db(tmp_path, monkeypatch):
    db_path = tmp_path / "debate_test.db"
    monkeypatch.setenv("TESTING", "1")

    import config

    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(config, "DEBATE_SKIP_UNCHANGED", True)
    monkeypatch.setattr(config, "DEBATE_WRITE_COMPOSITE", False)
    monkeypatch.setattr(config, "latest_trading_date", lambda db_path=None: "2026-05-31")

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE stocks (
            id INTEGER PRIMARY KEY,
            code TEXT,
            name TEXT,
            industry_sw TEXT,
            is_active INTEGER DEFAULT 1
        );
        CREATE TABLE comprehensive_scores (
            stock_id INTEGER,
            calc_date TEXT,
            fundamental_score REAL,
            technical_score REAL,
            sentiment_score REAL,
            composite_score REAL,
            capital_score REAL,
            policy_score REAL,
            mood_score REAL,
            val_score REAL
        );
        CREATE TABLE stock_news (
            stock_id INTEGER,
            title TEXT,
            sentiment_label TEXT,
            pub_date TEXT
        );
        CREATE TABLE tech_analysis_cache (
            stock_id INTEGER,
            signal TEXT,
            score REAL,
            created_at TEXT
        );
        CREATE TABLE debate_v2 (
            stock_id INTEGER,
            date TEXT,
            original_score REAL,
            adjusted_score REAL,
            debate_json TEXT,
            UNIQUE(stock_id, date)
        );
        CREATE TABLE stock_daily_quotes (trade_date TEXT, close REAL);

        INSERT INTO stocks VALUES (1, '600519', '茅台', '白酒', 1);
        INSERT INTO stocks VALUES (2, '000001', '平安', '银行', 1);

        INSERT INTO comprehensive_scores VALUES
            (1, '2026-05-31', 80, 70, 60, 75.0, 72, 65, 55, 68);
        INSERT INTO comprehensive_scores VALUES
            (2, '2026-05-31', 70, 65, 55, 68.0, 70, 60, 50, 66);

        INSERT INTO stock_news VALUES (1, '茅台提价', 'positive', '2026-05-30');
        INSERT INTO tech_analysis_cache VALUES (1, 'bullish', 72.0, '2026-05-31 10:00:00');
        INSERT INTO tech_analysis_cache VALUES (2, 'neutral', 55.0, '2026-05-31 10:00:00');
        """
    )
    conn.commit()
    conn.close()

    from services import score_sql

    monkeypatch.setattr(
        score_sql,
        "resolve_display_calc_date",
        lambda conn, min_ratio=0.5: "2026-05-31",
    )
    return db_path


def test_debate_input_hash_stable():
    from services.debate_v2 import debate_input_hash

    comp = {"composite_score": 75.0, "calc_date": "2026-05-31", "fundamental_score": 80}
    h1 = debate_input_hash(comp, ["新闻A"], {"score": 70})
    h2 = debate_input_hash(comp, ["新闻A"], {"score": 70})
    assert h1 == h2
    assert len(h1) == 16


def test_should_skip_when_score_unchanged(debate_db):
    from datetime import date

    from services.debate_context import preload_debate_context
    from services.debate_v2 import should_skip_debate

    today = date.today().strftime("%Y-%m-%d")
    conn = sqlite3.connect(debate_db)
    conn.execute(
        """INSERT INTO debate_v2 (stock_id, date, original_score, adjusted_score, debate_json)
           VALUES (1, ?, 75.0, 76.0, '{}')""",
        (today,),
    )
    conn.commit()
    conn.close()

    ctx = preload_debate_context()
    assert should_skip_debate(ctx, 1) is True
    assert should_skip_debate(ctx, 2) is False


def test_plan_dry_run(debate_db):
    from services.debate_context import preload_debate_context
    from services.debate_orchestrator import plan_debate_batch

    ctx = preload_debate_context()
    plan = plan_debate_batch(ctx, skip_unchanged=True)
    assert plan["total"] == 2
    assert plan["to_run"] == 2
    assert plan["skipped"] == 0


def test_can_enqueue_debate_when_idle():
    from services.job_queue import can_enqueue_debate_batch, find_active_debate_batch

    if find_active_debate_batch() is None:
        ok, _, _ = can_enqueue_debate_batch()
        assert ok is True


def test_run_debate_parallel_mock(debate_db, monkeypatch):
    from services.debate_batch_runner import run_debate_parallel
    from services.debate_context import preload_debate_context
    from services.debate_types import DebateTarget

    fake_debate = {
        "fundamental_analyst": {"score_adjust": 1},
        "technical_analyst": {"score_adjust": 0},
        "sentiment_analyst": {"score_adjust": 0},
        "capital_analyst": {"score_adjust": 0},
        "market_analyst": {"score_adjust": 0},
        "judge": {"final_score": 76, "verdict": "持有"},
    }

    def fake_chat(*_a, **_k):
        return json.dumps(fake_debate)

    monkeypatch.setattr("services.llm_client.chat_completion", fake_chat)

    ctx = preload_debate_context([1])
    targets = [DebateTarget(stock_id=1, code="600519", tier="priority", use_llm=True)]
    results = run_debate_parallel(targets, ctx, concurrency=1, skip_unchanged=False)
    assert len(results) == 1
    assert results[0].get("adjusted_score") is not None
    assert results[0].get("method") == "llm"
    assert "error" not in results[0]

    conn = sqlite3.connect(debate_db)
    row = conn.execute("SELECT adjusted_score FROM debate_v2 WHERE stock_id=1").fetchone()
    conn.close()
    assert row is not None


def test_run_light_debate_parallel(debate_db):
    from services.debate_batch_runner import run_debate_parallel
    from services.debate_context import preload_debate_context
    from services.debate_types import DebateTarget

    ctx = preload_debate_context([2])
    targets = [DebateTarget(stock_id=2, code="000001", tier="light", use_llm=False)]
    results = run_debate_parallel(targets, ctx, concurrency=1, skip_unchanged=False)
    assert len(results) == 1
    assert results[0].get("method") == "light_rules"
    assert results[0]["debate"]["fundamental_analyst"]["opinion"]
    assert results[0]["debate"]["_meta"]["tier"] == "light"


def test_normalize_compact_json():
    from services.debate_prompt import normalize_debate_json

    raw = {
        "fa": {"o": "基本面好", "a": 1, "r": "盈利", "c": 0.8},
        "j": {"v": "持有", "s": 72, "c": 0.7, "rk": "中", "act": "持有"},
    }
    out = normalize_debate_json(raw)
    assert out["fundamental_analyst"]["opinion"] == "基本面好"
    assert out["fundamental_analyst"]["score_adjust"] == 1
    assert out["judge"]["final_score"] == 72
    assert out["judge"]["risk"] == "中"


def test_tiered_plan_splits_llm_and_light(debate_db):
    import sqlite3

    conn = sqlite3.connect(debate_db)
    conn.execute(
        """INSERT INTO stocks VALUES (3, '600036', '招行', '银行', 1)"""
    )
    conn.execute(
        """INSERT INTO comprehensive_scores VALUES
           (3, '2026-05-31', 75, 70, 60, 90.0, 72, 65, 55, 68)"""
    )
    conn.commit()
    conn.close()

    from services.debate_context import preload_debate_context
    from services.debate_orchestrator import plan_debate_batch

    ctx = preload_debate_context()
    plan = plan_debate_batch(ctx, mode="tiered", skip_unchanged=False, priority_top_n=1, priority_bottom_n=1)
    assert plan["llm_count"] == 2
    assert plan["light_count"] == 1
    assert plan["tier_counts"].get("priority") == 2
    assert plan["tier_counts"].get("light") == 1


def test_mutual_exclusion_batch_fill_vs_debate(debate_db, monkeypatch):
    from services import job_queue
    from services.job_queue import (
        BATCH_FILL_JOB_TYPE,
        DEBATE_BATCH_JOB_TYPE,
        Job,
        JobStatus,
        can_enqueue_batch_fill,
        can_enqueue_debate_batch,
    )

    bf = Job(id="bf-test", job_type=BATCH_FILL_JOB_TYPE, payload={}, status=JobStatus.RUNNING)
    job_queue._jobs[bf.id] = bf
    ok, reason, _ = can_enqueue_debate_batch()
    assert ok is False
    assert "补算" in (reason or "")

    del job_queue._jobs[bf.id]
    db = Job(id="db-test", job_type=DEBATE_BATCH_JOB_TYPE, payload={}, status=JobStatus.RUNNING)
    job_queue._jobs[db.id] = db
    ok, reason, _ = can_enqueue_batch_fill()
    assert ok is False
    assert "辩论" in (reason or "")
    del job_queue._jobs[db.id]


def test_failures_from_result():
    from services.debate_retry import failures_from_result

    errs = failures_from_result(
        {"errors": [{"stock_id": 1, "error": "timeout"}], "results": [{"stock_id": 2}]}
    )
    assert len(errs) == 1
    assert errs[0]["stock_id"] == 1

    errs2 = failures_from_result({"results": [{"stock_id": 3, "error": "x"}]})
    assert errs2[0]["stock_id"] == 3


def test_merge_results_prefers_success():
    from services.debate_orchestrator import _merge_results_by_stock

    merged = _merge_results_by_stock(
        [
            {"stock_id": 1, "error": "fail"},
            {"stock_id": 1, "adjusted_score": 70},
        ]
    )
    assert len(merged) == 1
    assert merged[0]["adjusted_score"] == 70


def test_retry_failed_plan_from_job(debate_db, monkeypatch):
    from services import job_queue
    from services.debate_orchestrator import run_debate_batch
    from services.job_queue import Job, JobStatus

    job = Job(
        id="db-test-retry",
        job_type="debate_batch",
        payload={},
        status=JobStatus.DONE,
        result={
            "errors": [
                {"stock_id": 1, "code": "600519", "error": "timeout"},
                {"stock_id": 2, "code": "000001", "error": "timeout"},
            ]
        },
    )
    job_queue._jobs[job.id] = job
    job_queue._persist(job)

    plan = run_debate_batch(mode="retry_failed", retry_job_id="db-test-retry", dry_run=True)
    assert plan["to_run"] == 2
    assert plan["llm_count"] == 2
    assert plan["retry_info"]["source_job_id"] == "db-test-retry"
    del job_queue._jobs[job.id]


def test_llm_retryable_errors():
    from services.llm_client import _is_rate_limit, _is_retryable

    assert _is_retryable(RuntimeError("The read operation timed out")) is True
    assert _is_retryable(RuntimeError("LLM 请求失败 HTTP 429: rate")) is True
    assert _is_retryable(RuntimeError("LLM 返回空内容")) is False
    assert _is_rate_limit(RuntimeError("HTTP 429")) is True


def test_input_hash_changed_t2(debate_db):
    from datetime import date

    from services.debate_context import preload_debate_context
    from services.debate_tiered import assign_tier, input_hash_changed
    from services.debate_v2 import debate_input_hash

    today = date.today().strftime("%Y-%m-%d")
    ctx = preload_debate_context()
    comp = ctx.comprehensive[1]
    news_titles = [n.get("title", "") for n in ctx.news.get(1, [])]
    tech = ctx.tech.get(1, {})
    h = debate_input_hash(comp, news_titles, tech)
    debate_json = json.dumps({"_meta": {"input_hash": h, "tier": "light", "method": "light_rules"}})

    conn = sqlite3.connect(debate_db)
    conn.execute(
        """INSERT INTO debate_v2 (stock_id, date, original_score, adjusted_score, debate_json)
           VALUES (1, ?, 75.0, 76.0, ?)""",
        (today, debate_json),
    )
    conn.commit()
    conn.close()

    ctx2 = preload_debate_context()
    assert input_hash_changed(ctx2, 1) is False

    conn = sqlite3.connect(debate_db)
    conn.execute(
        "UPDATE comprehensive_scores SET sentiment_score=30 WHERE stock_id=1"
    )
    conn.commit()
    conn.close()

    ctx3 = preload_debate_context()
    assert input_hash_changed(ctx3, 1) is True
    tier, use_llm = assign_tier(1, mode="tiered", priority_ids=set(), ctx=ctx3)
    assert tier == "changed"
    assert use_llm is True


def test_needs_judge_escalation():
    import config
    from services.debate_llm_runner import needs_judge_escalation

    old = config.DEBATE_ESCALATE_SPREAD
    config.DEBATE_ESCALATE_SPREAD = 8
    try:
        assert needs_judge_escalation([0, 1, 2]) is False
        assert needs_judge_escalation([-5, 0, 5]) is True
    finally:
        config.DEBATE_ESCALATE_SPREAD = old


def test_two_phase_synthetic_judge(debate_db, monkeypatch):
    import config
    from services.debate_context import preload_debate_context
    from services.debate_v2 import enhanced_debate_with_context

    config.DEBATE_TWO_PHASE = True
    config.DEBATE_ESCALATE_SPREAD = 99
    calls: list[str] = []

    def fake_chat(user_prompt, **_k):
        calls.append(user_prompt[:20])
        if len(calls) == 1:
            return json.dumps(
                {
                    "fa": {"o": "好", "a": 1, "r": "x", "c": 0.7},
                    "ta": {"o": "平", "a": 0, "r": "x", "c": 0.7},
                    "sa": {"o": "平", "a": 0, "r": "x", "c": 0.7},
                    "ca": {"o": "平", "a": 0, "r": "x", "c": 0.7},
                    "ma": {"o": "平", "a": 0, "r": "x", "c": 0.7},
                }
            )
        return json.dumps(
            {
                "ra": {"o": "r", "rl": "中", "kr": "k"},
                "rc": {"o": "r", "rl": "中", "kr": "k"},
                "rn": {"o": "r", "rl": "中", "kr": "k"},
                "j": {"v": "持有", "s": 76, "c": 0.7, "rk": "中", "act": "持有"},
            }
        )

    monkeypatch.setattr("services.llm_client.chat_completion", fake_chat)
    ctx = preload_debate_context([1])
    result = enhanced_debate_with_context(ctx, 1, "600519", skip_unchanged=False, use_llm=True)
    assert "error" not in result
    assert result["debate"]["judge"]["final_score"] is not None
    assert len(calls) == 1
    assert result["debate"]["_meta"].get("judge_escalated") is False


def test_debate_batch_log(debate_db):
    from services.debate_batch_log import log_batch_done, log_batch_start

    plan = {
        "calc_date": "2026-05-31",
        "today": "2026-05-31",
        "total": 2,
        "to_run": 2,
        "llm_count": 1,
        "light_count": 1,
        "skipped": 0,
        "concurrency": 4,
        "tier_counts": {"priority": 1, "light": 1},
        "skip_reasons": {},
    }
    start_id = log_batch_start(job_id="j1", mode="tiered", plan=plan)
    done_id = log_batch_done(
        job_id="j1",
        mode="tiered",
        plan=plan,
        result={"completed": 2, "errors": [], "duration_ms": 100, "batch_retry_passes": 0},
    )
    assert start_id > 0 and done_id > 0

    conn = sqlite3.connect(debate_db)
    rows = conn.execute("SELECT event_type FROM debate_batch_log ORDER BY id").fetchall()
    conn.close()
    assert [r[0] for r in rows] == ["start", "done"]

