#!/usr/bin/env python3
"""阶段 IV 全量启用 — 评分历史 / ML 训练 / 合成 / 签核"""
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

_env_path = os.path.join(ROOT, "backend", ".env")
if os.path.isfile(_env_path):
    with open(_env_path, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

from services.score_history_expand import expand_score_history  # noqa: E402
from services.factor_merge_preset import run_preset_merges  # noqa: E402
from services.ml_predictions import run_qlib_train_job, sync_ml_to_comprehensive  # noqa: E402
from services.upgrade_monitor import get_upgrade_dashboard  # noqa: E402


def main() -> int:
    score = expand_score_history(days=90)
    merge = run_preset_merges()
    ml_train = run_qlib_train_job({"train_days": 120})
    ml_sync = sync_ml_to_comprehensive() if ml_train.get("status") == "done" else {"skipped": True}
    snap = subprocess.run(["bash", os.path.join(ROOT, "scripts", "db_snapshot.sh")], cwd=ROOT, capture_output=True, text=True)
    bt = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "reconcile_backtest.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    metrics = get_upgrade_dashboard()
    report = {
        "signed_at": "2026-05-31",
        "phase": "IV_full_release",
        "score_history": score,
        "merge": {"success_count": merge.get("success_count"), "presets_run": merge.get("presets_run")},
        "ml_train": ml_train,
        "ml_sync": ml_sync,
        "db_snapshot": snap.returncode == 0,
        "backtest_reconcile_ok": bt.returncode == 0,
        "upgrade_metrics": metrics,
        "env": {
            "gray_release_pct": int(os.getenv("AFR_GRAY_RELEASE_PCT", "0")),
            "factor_merge": os.getenv("AFR_FACTOR_MERGE_ENABLED"),
            "rust_approved": os.getenv("AFR_RUST_BACKTEST_APPROVED"),
            "qlib_enabled": os.getenv("AFR_QLIB_ENABLED"),
        },
    }
    out = os.path.join(ROOT, "docs", "reconciliation", "phase4_signoff.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if metrics.get("all_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
