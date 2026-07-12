#!/usr/bin/env python3
"""验证 QARS2 / qars3 是否可用（在 venv-quant 中运行）"""
from __future__ import annotations

import json
import sys


def main() -> int:
    report: dict = {"qars3_import": False, "has_qars_support": False}

    try:
        import qars3

        report["qars3_import"] = True
        report["qars3_version"] = getattr(qars3, "__version__", "unknown")
        report["has_QA_QIFIAccount"] = hasattr(qars3, "QA_QIFIAccount")
        report["has_Backtest"] = hasattr(qars3, "Backtest")
    except ImportError as e:
        report["qars3_error"] = str(e)

    qa_root = None
    try:
        import QUANTAXIS  # noqa: F401
        from QUANTAXIS.QARSBridge import has_qars_support

        report["has_qars_support"] = bool(has_qars_support())
        qa_root = True
    except ImportError:
        qa_root = False

    if qa_root is False:
        # 仅 qars3，不装完整 QUANTAXIS 时
        report["has_qars_support"] = report.get("qars3_import", False)
        report["note"] = "QUANTAXIS 未安装；以 qars3 import 为准"

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report.get("has_qars_support"):
        print("✅ QARS2已安装")
        return 0
    print("❌ QARS2未安装")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
