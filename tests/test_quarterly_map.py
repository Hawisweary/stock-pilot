import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "backend"))

from services.data_processor import is_quarterly_report_type, map_report_type


def test_map_report_type_quarterly_months():
    assert map_report_type("", "2024-03-31") == "q1"
    assert map_report_type("", "2024-06-30") == "q2"
    assert map_report_type("", "2024-12-31") == "annual"


def test_is_quarterly_report_type():
    assert is_quarterly_report_type("q1", "2024-03-31")
    assert not is_quarterly_report_type("annual", "2024-12-31")
