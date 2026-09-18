"""recon-dispute-agent: an AI agent that investigates payment reconciliation
discrepancies emitted by the payment-reconciliation-engine.

Public surface:

    from recon_agent import DisputeAgent, build_agent, load_report

    report = load_report("report.json")
    agent = build_agent("offline")          # or "anthropic"
    result = agent.run(report)
"""

from __future__ import annotations

from pathlib import Path

from .agent import DisputeAgent
from .knowledge import KnowledgeBase
from .models import (
    Discrepancy,
    Investigation,
    InvestigationReport,
    RecommendedAction,
    Report,
    Resolution,
    RootCause,
    VerificationResult,
)
from .runtime import build_agent, make_llm

__all__ = [
    "Discrepancy",
    "DisputeAgent",
    "Investigation",
    "InvestigationReport",
    "KnowledgeBase",
    "RecommendedAction",
    "Report",
    "Resolution",
    "RootCause",
    "VerificationResult",
    "build_agent",
    "load_report",
    "make_llm",
]

__version__ = "0.1.0"


def load_report(source: str | Path) -> Report:
    """Load an engine report from a JSON string, a path string, or a Path.

    A ``str`` whose first non-space character is ``{`` is treated as inline JSON;
    anything else is treated as a filesystem path.
    """
    if isinstance(source, Path):
        return Report.model_validate_json(source.read_text())
    if source.lstrip().startswith("{"):
        return Report.model_validate_json(source)
    return Report.model_validate_json(Path(source).read_text())
