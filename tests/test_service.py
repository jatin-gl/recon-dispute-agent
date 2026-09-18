"""HTTP surface tests via FastAPI's TestClient (offline agent)."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from recon_agent.service import app

client = TestClient(app)
FIXTURES = Path(__file__).parent / "fixtures"


def test_healthz():
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_investigate_endpoint():
    payload = json.loads((FIXTURES / "example-report.json").read_text())
    resp = client.post("/v1/investigate", json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source_report_id"] == payload["report_id"]
    assert len(body["investigations"]) == 6
    for inv in body["investigations"]:
        assert inv["resolution"]["root_cause"]
        assert "approved" in inv["verification"]


def test_investigate_rejects_malformed_report():
    resp = client.post("/v1/investigate", json={"not": "a report"})
    assert resp.status_code == 422  # pydantic validation error
