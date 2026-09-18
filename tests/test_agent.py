"""End-to-end agent tests using the deterministic offline brain.

These exercise the real control flow — the tool-use loop, evidence gathering, the
verifier pass, and escalation — without any network call.
"""

from recon_agent import DisputeAgent
from recon_agent.knowledge import KnowledgeBase
from recon_agent.llm import HeuristicClient
from recon_agent.models import (
    Discrepancy,
    Money,
    RecommendedAction,
    Report,
    RootCause,
    Severity,
    Transaction,
)


def test_run_investigates_every_discrepancy(agent: DisputeAgent, report: Report):
    result = agent.run(report)
    assert len(result.investigations) == len(report.discrepancies) == 6
    assert result.source_report_id == report.report_id


def test_expected_root_causes(agent: DisputeAgent, report: Report):
    result = agent.run(report)
    by_key = {inv.match_key: inv for inv in result.investigations}

    expected = {
        "TXN-1007": (RootCause.DUPLICATE_WEBHOOK, RecommendedAction.DEDUPE_LEDGER_ENTRY),
        "TXN-1004": (RootCause.MISSING_CAPTURE, RecommendedAction.REVERSE_LEDGER_ENTRY),
        "TXN-1003": (RootCause.TIMING_LAG, RecommendedAction.NO_ACTION_TIMING),
        "TXN-1002": (RootCause.ROUNDING_OR_FX, RecommendedAction.ADJUST_LEDGER_AMOUNT),
        "TXN-1005": (RootCause.FEE_SCHEDULE_DRIFT, RecommendedAction.UPDATE_FEE_RECORD),
        "TXN-1006": (RootCause.FAILED_REFUND, RecommendedAction.REISSUE_REFUND),
    }
    for key, (cause, action) in expected.items():
        inv = by_key[key]
        assert inv.resolution.root_cause == cause, f"{key}: {inv.resolution}"
        assert inv.resolution.recommended_action == action, f"{key}: {inv.resolution}"


def test_evidence_gathered_and_bounded_steps(agent: DisputeAgent, report: Report):
    result = agent.run(report)
    for inv in result.investigations:
        # The offline brain fetches all three systems of record, then submits.
        assert len(inv.evidence) == 3
        assert inv.steps == 2
        assert any("get_events" in e for e in inv.evidence)


def test_well_supported_findings_are_verified_and_not_escalated(agent: DisputeAgent, report: Report):
    result = agent.run(report)
    assert result.escalated_count == 0
    for inv in result.investigations:
        assert inv.verification.approved is True
        assert inv.resolution.confidence >= 0.6


def test_unknown_transaction_escalates_to_manual_review():
    # A discrepancy whose reference the knowledge base has never seen: the agent
    # can gather no explanatory evidence, so it must escalate rather than guess.
    disc = Discrepancy(
        id="disc_x",
        type="AMOUNT_MISMATCH",
        severity=Severity.HIGH,
        match_key="UNSEEN-9999",
        psp_record=Transaction(
            match_key="UNSEEN-9999", external_id="p", source="psp",
            amount=Money(amount_minor=10000, currency="USD"), fee=Money(amount_minor=0, currency="USD"),
            status="settled",
        ),
        ledger_record=Transaction(
            match_key="UNSEEN-9999", external_id="l", source="ledger",
            amount=Money(amount_minor=9000, currency="USD"), fee=Money(amount_minor=0, currency="USD"),
            status="settled",
        ),
        monetary_impact=Money(amount_minor=1000, currency="USD"),
        detail="amount mismatch with no known context",
    )
    agent = DisputeAgent(llm=HeuristicClient(), knowledge=KnowledgeBase.default())
    inv = agent.investigate(disc)

    assert inv.resolution.root_cause == RootCause.UNKNOWN
    assert inv.resolution.recommended_action == RecommendedAction.MANUAL_REVIEW
    assert inv.escalated is True
    assert inv.verification.approved is False


def test_verifier_rejects_low_confidence_even_with_a_cause():
    # Directly exercise the verifier: a real cause but weak confidence must be rejected.
    from recon_agent.models import Resolution

    agent = DisputeAgent(llm=HeuristicClient(), knowledge=KnowledgeBase.default())
    disc = Discrepancy(
        id="d", type="FEE_MISMATCH", severity=Severity.LOW, match_key="TXN-1005",
        monetary_impact=Money(amount_minor=50, currency="USD"), detail="fee",
    )
    weak = Resolution(
        root_cause=RootCause.FEE_SCHEDULE_DRIFT, confidence=0.2,
        recommended_action=RecommendedAction.UPDATE_FEE_RECORD, rationale="hunch",
    )
    verdict = agent.verify(disc, weak, evidence=["get_events(...) -> {...}"])
    assert verdict.approved is False
