"""regime_pipeline 单元测试。"""
import os
import sqlite3
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.regime_pipeline import run_regime_l2_l3_pipeline


def test_pipeline_skips_matrix_when_disabled():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    conn = sqlite3.connect(f.name)

    with patch("services.regime_pipeline.sync_regime") as mock_sync, patch(
        "services.regime_pipeline.generate_and_persist_recommendation",
    ) as mock_l3, patch("services.regime_pipeline.refresh_strategy_regime_matrix") as mock_l2:
        mock_sync.return_value = {"regime_bucket_csi800": "oscillation"}
        mock_l3.return_value = {
            "trade_date": "2026-07-27",
            "market": {"regime_bucket": "oscillation"},
            "recommendation": {"primary": {"strategy": "composite"}},
        }

        result = run_regime_l2_l3_pipeline(conn, refresh_matrix=False)

    assert result["ok"] is True
    assert result["matrix_refreshed"] is False
    mock_l2.assert_not_called()
    mock_sync.assert_called_once()
    mock_l3.assert_called_once()
    conn.close()


def test_pipeline_refreshes_matrix_when_enabled():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    conn = sqlite3.connect(f.name)

    with patch("services.regime_pipeline.sync_regime") as mock_sync, patch(
        "services.regime_pipeline.generate_and_persist_recommendation",
    ) as mock_l3, patch("services.regime_pipeline.refresh_strategy_regime_matrix") as mock_l2:
        mock_sync.return_value = {"regime_bucket_csi800": "high_vol"}
        mock_l2.return_value = {"updated_cells": 24, "as_of_date": "2026-07-27"}
        mock_l3.return_value = {
            "trade_date": "2026-07-27",
            "market": {"regime_bucket": "high_vol"},
            "recommendation": {"primary": {"strategy": "composite"}},
        }

        result = run_regime_l2_l3_pipeline(conn, refresh_matrix=True)

    assert result["matrix_refreshed"] is True
    mock_l2.assert_called_once()
    conn.close()
