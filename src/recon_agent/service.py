"""FastAPI service exposing the agent.

    GET  /healthz            liveness
    POST /v1/investigate     body = engine Report JSON -> InvestigationReport

The mode (offline vs. Claude-backed) is selected by the RECON_AGENT_MODE
environment variable; it defaults to ``offline`` so the service runs with no API
key. Run with: ``uvicorn recon_agent.service:app``.
"""

from __future__ import annotations

from fastapi import FastAPI

from .models import InvestigationReport, Report
from .runtime import build_agent

app = FastAPI(
    title="recon-dispute-agent",
    version="0.1.0",
    summary="Investigates payment reconciliation discrepancies with an LLM agent + verifier.",
)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/investigate", response_model=InvestigationReport)
def investigate(report: Report) -> InvestigationReport:
    """Investigate every discrepancy in an engine report and return the findings."""
    agent = build_agent()
    return agent.run(report)
