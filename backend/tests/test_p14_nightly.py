"""P14 夜间任务修复:轮转抓取执行器、公告分批提交、夜间预设、分步日志、调度状态。"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.rotating_fetch import load_cursor, rotate_after, run_rotating_fetch  # noqa: E402

ROWS = [(i, f"{i:06d}") for i in range(1, 11)]


def test_rotate_after():
    assert rotate_after(ROWS, None) == ROWS
    assert [r[0] for r in rotate_after(ROWS, 4)] == [5, 6, 7, 8, 9, 10, 1, 2, 3, 4]
    assert [r[0] for r in rotate_after(ROWS, 10)] == list(range(1, 11))  # 到末尾后绕回开头
    assert [r[0] for r in rotate_after(ROWS, 99)] == list(range(1, 11))


def test_full_run_processes_in_order_and_saves_cursor(tmp_path):
    cur = str(tmp_path / "cursors.json")
    stored: list[int] = []
    r = run_rotating_fetch(
        ROWS,
        fetch=lambda code: int(code),
        store=lambda sid, v: (stored.append(sid), 1)[1],
        workers=3,
        cursor_key="t",
        cursor_path=cur,
    )
    assert stored == list(range(1, 11))  # 并发抓取,但入库严格按顺序
    assert r["processed"] == 10 and r["added"] == 10 and r["complete_cycle"]
    assert not r["stopped_by_budget"]
    assert load_cursor("t", cur) == 10


def test_budget_stops_early_and_next_run_resumes(tmp_path):
    cur = str(tmp_path / "cursors.json")
    first: list[int] = []
    r1 = run_rotating_fetch(
        ROWS, fetch=lambda c: c, store=lambda sid, v: first.append(sid),
        workers=1, budget_s=0, cursor_key="t", cursor_path=cur,
    )
    # 预算为 0:只处理已派发的(workers*2 = 2 只),然后停下并记下游标
    assert r1["stopped_by_budget"] and not r1["complete_cycle"]
    assert first == [1, 2] and load_cursor("t", cur) == 2

    second: list[int] = []
    run_rotating_fetch(
        ROWS, fetch=lambda c: c, store=lambda sid, v: second.append(sid),
        workers=1, cursor_key="t", cursor_path=cur,
    )
    assert second == [3, 4, 5, 6, 7, 8, 9, 10, 1, 2]  # 从上次停下处继续,绕一圈


def test_errors_do_not_stop_the_run():
    def fetch(code):
        if code == "000003":
            raise RuntimeError("boom")
        return code

    r = run_rotating_fetch(ROWS, fetch=fetch, store=lambda sid, v: 1, workers=2)
    assert r["processed"] == 10 and r["added"] == 9 and r["error_count"] == 1
    assert r["errors"][0].startswith("000003:")


@pytest.fixture()
def ann_db(tmp_path, monkeypatch):
    db = tmp_path / "afr.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE stocks (id INTEGER PRIMARY KEY, code TEXT, is_active INTEGER)")
    conn.executemany("INSERT INTO stocks VALUES (?,?,1)", [(i, f"{i:06d}") for i in range(1, 121)])
    conn.commit()
    conn.close()
    import services.announcement_fetch as af

    monkeypatch.setattr(af, "DB_PATH", str(db))
    return db, af


def test_announcements_commit_in_batches(ann_db, monkeypatch):
    """回归:原来 5000+ 只全部抓完才提交,写事务开约 2 小时。现在每 50 只提交一次。"""
    db, af = ann_db
    seen_committed: dict[str, int] = {}

    def fake_fetch(code, limit=30):
        if code == "000060":  # 此时前 50 只应已提交,别的连接能看到
            c = sqlite3.connect(db)
            seen_committed["at_60"] = c.execute("SELECT COUNT(*) FROM stock_announcements").fetchone()[0]
            c.close()
        return [{"title": f"公告{code}-{k}", "pub_date": "2026-11-20", "art_code": f"{code}-{k}"} for k in range(2)]

    monkeypatch.setattr(af, "fetch_announcements_for_stock", fake_fetch)
    r = af.sync_all_announcements(sleep_s=0, workers=1)
    assert r["processed"] == 120 and r["added"] == 240
    assert seen_committed["at_60"] >= 100  # 至少第一批 50 只 × 2 条已落盘
    c = sqlite3.connect(db)
    assert c.execute("SELECT COUNT(*) FROM stock_announcements").fetchone()[0] == 240
    c.close()


def test_nightly_preset_llm_off_and_rotating_fetch_on():
    from services.v5_data_sync import V5_SYNC_MODE_PRESETS

    p = V5_SYNC_MODE_PRESETS["nightly"]
    assert p["use_llm_events"] is False
    assert p["fetch_cursor"] is True and p["fetch_workers"] >= 1
    assert p["announcement_budget_s"] + p["news_budget_s"] <= 2 * 3600


def test_global_policy_scan_off_by_default(monkeypatch):
    import config
    import services.policy_global as pg
    import services.v5_data_sync as vds

    monkeypatch.setattr(config, "NIGHTLY_GLOBAL_POLICY_SCAN", False)
    monkeypatch.setattr(vds, "sync_v5_data_sources", lambda **kw: {"steps": {}, "ok": True})

    def _boom():
        raise AssertionError("默认不应调用 LLM 政策扫描")

    monkeypatch.setattr(pg, "global_policy_scan", _boom)
    r = vds.sync_v5_nightly_fetch()
    assert "skipped" in r["policy_snapshot"]


def test_step_runner_logs_and_keeps_going():
    from services.v5_data_sync import _StepRunner

    lines: list[str] = []
    result: dict = {"steps": {}}
    step = _StepRunner(result, lines.append)
    step("good", lambda: {"processed": 3, "added": 1})
    step("bad", lambda: (_ for _ in ()).throw(RuntimeError("网络断了")))
    assert result["steps"]["good"]["added"] == 1
    assert result["steps"]["bad"] == {"error": "网络断了"}
    assert any(ln.startswith("✓ good") and "added=1" in ln for ln in lines)
    assert any(ln.startswith("✗ bad") for ln in lines)


def test_scheduler_status_helpers():
    from services.scheduler import _status_from_error, _status_from_output

    assert _status_from_output('{"job": "nightly", "ok": true}') == "ok"
    assert _status_from_output('{"job": "nightly", "ok": false}') == "partial"
    assert _status_from_output("not json") == "ok"
    assert _status_from_error(RuntimeError("timeout after 10800s")) == "timeout"
    assert _status_from_error(RuntimeError("exit=-9 stderr=")) == "timeout"
    assert _status_from_error(RuntimeError("exit=1 stderr=Traceback")) == "failed"
