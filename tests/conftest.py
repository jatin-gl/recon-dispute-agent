from pathlib import Path

import pytest

from recon_agent import DisputeAgent, load_report
from recon_agent.knowledge import KnowledgeBase
from recon_agent.llm import HeuristicClient
from recon_agent.models import Report

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def report() -> Report:
    return load_report(FIXTURES / "example-report.json")


@pytest.fixture
def knowledge() -> KnowledgeBase:
    return KnowledgeBase.default()


@pytest.fixture
def agent(knowledge: KnowledgeBase) -> DisputeAgent:
    return DisputeAgent(llm=HeuristicClient(), knowledge=knowledge)
