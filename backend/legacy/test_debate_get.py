"""GET /api/debate/{stock_id} 回归"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app import app


def test_get_debate_stock_26():
    client = TestClient(app)
    r = client.get("/api/debate/26")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("stock_id") == 26
    if body.get("debate"):
        assert "fundamental_analyst" in body["debate"]
