"""FastAPI service exposing the agent.

    GET  /healthz            liveness
    POST /v1/investigate     body = engine Report JSON -> InvestigationReport

The mode (offline vs. Claude-backed) is selected by the RECON_AGENT_MODE
environment variable; it defaults to ``offline`` so the service runs with no API
key. Run with: ``uvicorn recon_agent.service:app``.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException

from .knowledge import KnowledgeBase
from .models import InvestigationReport, Report
from .runtime import build_agent

logger = logging.getLogger(__name__)

app = FastAPI(
    title="recon-dispute-agent",
    version="0.1.0",
    summary="Investigates payment reconciliation discrepancies with an LLM agent + verifier.",
)

# The seeded knowledge base is read-only; build it once and share it across
# requests rather than rebuilding the dataset on every call.
_KNOWLEDGE = KnowledgeBase.default()


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/investigate", response_model=InvestigationReport)
def investigate(report: Report) -> InvestigationReport:
    """Investigate every discrepancy in an engine report and return the findings."""
    agent = build_agent(knowledge=_KNOWLEDGE)
    try:
        return agent.run(report)
    except Exception as exc:  # e.g. Claude unreachable/unauthenticated in anthropic mode
        # Log the detail server-side; return a clean, generic 502 without leaking
        # internal/backend error text (or a 500 traceback) to the caller.
        logger.exception("agent execution failed")
        raise HTTPException(status_code=502, detail="agent execution failed") from exc
