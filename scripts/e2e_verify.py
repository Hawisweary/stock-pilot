#!/usr/bin/env python3
"""端到端验证：全量因子 → 增量 → batch-fill → GP"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

BASE = os.environ.get("AFR_API_BASE", "http://localhost:8800/api")


def call(method: str, path: str, body: dict | None = None, timeout: int = 300) -> dict:
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            elapsed = round((time.perf_counter() - t0) * 1000)
            out = json.loads(raw) if raw else {}
            out["_elapsed_ms"] = elapsed
            return out
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            out = json.loads(raw)
        except json.JSONDecodeError:
            out = {"error": raw[:500], "status": e.code}
        out["_elapsed_ms"] = round((time.perf_counter() - t0) * 1000)
        out["_http_status"] = e.code
        return out
    except Exception as e:
        return {"error": str(e), "_elapsed_ms": round((time.perf_counter() - t0) * 1000)}


def main() -> int:
    results: dict[str, dict] = {}

    print("=== 1. Health ===")
    results["health"] = call("GET", "/health", timeout=10)
    print(json.dumps(results["health"], ensure_ascii=False, indent=2))

    print("\n=== 2. Migration (local) ===")
    try:
        import sqlite3
        import config
        from migrations import run_migrations

        conn = sqlite3.connect(config.DB_PATH)
        v = run_migrations(conn)
        conn.close()
        results["migration"] = {"schema_version": v, "db": config.DB_PATH}
        print(json.dumps(results["migration"], ensure_ascii=False))
    except Exception as e:
        results["migration"] = {"error": str(e)}
        print(results["migration"])

    print("\n=== 3. 全量因子计算 ===")
    results["factor_full"] = call("POST", "/factors/compute?mode=full", timeout=600)
    print(json.dumps({k: results["factor_full"].get(k) for k in (
        "date", "factors_computed", "backfill", "wide_rows", "error", "_elapsed_ms"
    )}, ensure_ascii=False, indent=2))

    print("\n=== 4. 增量因子计算 ===")
    results["factor_incremental"] = call("POST", "/factors/compute?mode=incremental", timeout=120)
    print(json.dumps({k: results["factor_incremental"].get(k) for k in (
        "mode", "target_date", "stocks_touched", "cells_written", "duration_ms", "error", "_elapsed_ms"
    )}, ensure_ascii=False, indent=2))

    print("\n=== 5. Batch-fill dry-run ===")
    results["batch_dry"] = call(
        "POST",
        "/scores/batch-fill",
        {"mode": "compute_and_sync", "dry_run": True},
        timeout=60,
    )
    print(json.dumps({
        "target_date": results["batch_dry"].get("target_date"),
        "sync_rate_required": results["batch_dry"].get("sync_rate_required"),
        "planned_actions": len(results["batch_dry"].get("planned", [])),
        "error": results["batch_dry"].get("error"),
        "_elapsed_ms": results["batch_dry"].get("_elapsed_ms"),
    }, ensure_ascii=False, indent=2))

    print("\n=== 6. Batch-fill sync_only (async job) ===")
    job_resp = call(
        "POST",
        "/scores/batch-fill",
        {"mode": "sync_only", "dry_run": False},
        timeout=30,
    )
    job_id = job_resp.get("job_id")
    batch_sync: dict = {"enqueue": job_resp}
    if job_id:
        for _ in range(60):
            time.sleep(2)
            st = call("GET", f"/system/jobs/{job_id}", timeout=15)
            batch_sync["poll"] = st
            status = st.get("status")
            if status in ("done", "failed", "cancelled"):
                batch_sync.update(st.get("result") or {})
                break
    else:
        batch_sync["error"] = job_resp.get("error") or job_resp.get("detail")
    results["batch_sync"] = batch_sync
    print(json.dumps({
        "job_id": results["batch_sync"].get("enqueue", {}).get("job_id"),
        "status": results["batch_sync"].get("poll", {}).get("status"),
        "target_date": results["batch_sync"].get("target_date"),
        "sync_rate_required_after": results["batch_sync"].get("sync_rate_required_after"),
        "filled_count": results["batch_sync"].get("filled_count"),
        "error": results["batch_sync"].get("error"),
    }, ensure_ascii=False, indent=2))

    print("\n=== 7. 表达式校验 ===")
    results["expr_validate"] = call(
        "POST",
        "/factors/expressions/validate",
        {"formula": "Mean($adj_close, 20) / Std($adj_close, 20)"},
        timeout=15,
    )
    print(json.dumps(results["expr_validate"], ensure_ascii=False, indent=2))

    print("\n=== 8. GP 搜索 (小规模) ===")
    results["gp"] = call(
        "POST",
        "/factors/gp/run",
        {"population": 4, "generations": 2, "top_k": 2, "async_mode": False},
        timeout=600,
    )
    winners = results["gp"].get("winners") or []
    print(json.dumps({
        "run_id": results["gp"].get("run_id"),
        "evaluated": results["gp"].get("evaluated"),
        "winners": [
            {"factor_id": w.get("factor_id"), "mean_ic": w.get("mean_ic"), "formula": w.get("formula")}
            for w in winners[:3]
        ],
        "error": results["gp"].get("error"),
        "_elapsed_ms": results["gp"].get("_elapsed_ms"),
    }, ensure_ascii=False, indent=2))

    print("\n=== 9. debate_batch_log 查询 ===")
    results["debate_history"] = call("GET", "/debate/batch/history?limit=3", timeout=15)
    print(json.dumps({
        "count": len(results["debate_history"].get("history", [])),
        "sample": (results["debate_history"].get("history") or [])[:1],
        "_elapsed_ms": results["debate_history"].get("_elapsed_ms"),
    }, ensure_ascii=False, indent=2))

    ok = True
    for key in ("factor_full", "factor_incremental", "batch_sync", "expr_validate", "gp"):
        if results.get(key, {}).get("error"):
            ok = False
    print("\n=== SUMMARY ===")
    print("PASS" if ok else "PARTIAL/FAIL")
    out_path = os.path.join(ROOT, "scripts", "e2e_verify_result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"详细结果: {out_path}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
