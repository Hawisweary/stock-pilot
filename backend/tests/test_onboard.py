"""onboard 与东财财报模块单元测试"""
from unittest.mock import MagicMock, patch

import pandas as pd


def test_register_stock_adds_new():
    from services.onboard_service import register_stock

    with patch("services.onboard_service.execute_sql", return_value=[]), patch(
        "services.onboard_service.execute_insert", return_value=99
    ):
        r = register_stock("300450", "A", skip_existing=True)
    assert r["status"] == "added"
    assert r["stock_id"] == 99


def test_register_stock_skips_active():
    from services.onboard_service import register_stock

    with patch(
        "services.onboard_service.execute_sql",
        return_value=[{"id": 1, "is_active": 1}],
    ):
        r = register_stock("600519", "A", skip_existing=True)
    assert r["status"] == "skipped"


def test_to_secucode():
    from services.eastmoney_finance import to_secucode

    assert to_secucode("600519") == "600519.SH"
    assert to_secucode("300450") == "300450.SZ"


def test_fetch_profit_sheet_parses_em_response():
    from services import eastmoney_finance as em

    date_json = {"data": [{"REPORT_DATE": "2024-12-31 00:00:00"}]}
    sheet_json = {
        "data": [
            {
                "REPORT_DATE": "2024-12-31 00:00:00",
                "REPORT_TYPE": "年报",
                "TOTAL_OPERATE_INCOME": 100,
                "OPERATE_INCOME": 90,
            }
        ]
    }
    mock_resp = MagicMock()
    mock_resp.json.side_effect = [date_json, sheet_json]
    mock_resp.text = '<input id="hidctype" type="hidden" value="4" />'

    with patch.object(em, "http_get", return_value=mock_resp), patch.object(
        em, "time", MagicMock(sleep=lambda *_: None)
    ):
        df = em.fetch_profit_sheet("300450", "yearly")

    assert not df.empty
    assert "TOTAL_OPERATE_INCOME" in df.columns
