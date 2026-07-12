#!/usr/bin/env python3
"""阶段 III：IC 审查 + 预设因子合成 + 签核指标"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

# 先加载 .env，再 import backend 模块
_env_path = os.path.join(ROOT, "backend", ".env")
if os.path.isfile(_env_path):
    with open(_env_path, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

from services.ic_stability import review_factor_ic  # noqa: E402
from services.factor_merge_preset import run_preset_merges  # noqa: E402
from services.upgrade_monitor import get_upgrade_dashboard  # noqa: E402


def main() -> int:
    ic = review_factor_ic()
    merge = run_preset_merges()
    metrics = get_upgrade_dashboard()
    report = {
        "phase": "III_factor_merge",
        "ic_review": ic,
        "merge": merge,
        "upgrade_metrics": {
            "all_ok": metrics.get("all_ok"),
            "factor_merge_ready": metrics["migration"]["gates"]["factor_merge_ready"],
            "ic_stable_ready": metrics["migration"]["gates"]["ic_stable_ready"],
            "factor_history_days": metrics["migration"]["factor_history_days"],
        },
    }
    out_path = os.path.join(ROOT, "docs", "reconciliation", "phase3_signoff.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if merge.get("success_count", 0) >= 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
