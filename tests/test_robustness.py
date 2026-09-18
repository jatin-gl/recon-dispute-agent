"""Robustness: JSON extraction edge cases, malformed-payload fallback, stdin."""

import io

from recon_agent import DisputeAgent
from recon_agent.agent import _resolution_from
from recon_agent.knowledge import KnowledgeBase
from recon_agent.llm import HeuristicClient, LLMResponse, ToolCall, _extract_json_object
from recon_agent.models import (
    Discrepancy,
    Money,
    RecommendedAction,
    RootCause,
    Severity,
)


def test_extract_json_object_with_braces_inside_strings():
    # An evidence value containing braces must not confuse extraction.
    text = (
        'CONTEXT:\n'
        '{"resolution": {"root_cause": "TIMING_LAG", "confidence": 0.85, '
        '"recommended_action": "NO_ACTION_TIMING", "rationale": "ok"}, '
        '"evidence": ["get_events({\\"reference\\":\\"X\\"}) -> {\\"found\\": true}"]}\n'
        'trailing prose that should be ignored {not json'
    )
    obj = _extract_json_object(text)
    assert obj is not None
    assert obj["resolution"]["root_cause"] == "TIMING_LAG"
    assert obj["evidence"][0].endswith('{"found": true}')


def test_extract_json_object_none_when_absent():
    assert _extract_json_object("no json here at all") is None


def test_resolution_from_none_falls_back_to_manual_review():
    r = _resolution_from(None)
    assert r.root_cause == RootCause.UNKNOWN
    assert r.recommended_action == RecommendedAction.MANUAL_REVIEW


def test_resolution_from_malformed_payload_falls_back():
    # An off-schema payload (invalid enum) must degrade, not raise.
    r = _resolution_from({"root_cause": "NOT_A_REAL_CAUSE", "confidence": 2.0,
                          "recommended_action": "???", "rationale": ""})
    assert r.root_cause == RootCause.UNKNOWN
    assert r.recommended_action == RecommendedAction.MANUAL_REVIEW


class MalformedInvestigatorClient:
    """Investigator that gathers evidence, then submits an invalid resolution.

    Verifier calls (submit_verification available) are delegated to the real
    offline brain so only the investigator output is malformed.
    """

    def __init__(self):
        self._real = HeuristicClient()

    def complete(self, system, messages, tools):
        tool_names = {t["name"] for t in tools}
        if "submit_verification" in tool_names:
            return self._real.complete(system, messages, tools)
        # investigator: first gather evidence via the real brain...
        resp = self._real.complete(system, messages, tools)
        # ...but when it would submit, replace the payload with garbage.
        for call in resp.tool_calls:
            if call.name == "submit_resolution":
                bad = ToolCall(id=call.id, name="submit_resolution",
                               input={"root_cause": "BOGUS", "confidence": 5,
                                      "recommended_action": "NOPE", "rationale": ""})
                return LLMResponse(
                    stop_reason="tool_use",
                    tool_calls=[bad],
                    assistant_content=[{"type": "tool_use", "id": bad.id, "name": bad.name, "input": bad.input}],
                )
        return resp


def _sample_discrepancy() -> Discrepancy:
    return Discrepancy(
        id="d", type="FEE_MISMATCH", severity=Severity.LOW, match_key="TXN-1005",
        monetary_impact=Money(amount_minor=50, currency="USD"), detail="fee",
    )


def test_agent_survives_malformed_model_output_and_escalates():
    agent = DisputeAgent(llm=MalformedInvestigatorClient(), knowledge=KnowledgeBase.default())
    inv = agent.investigate(_sample_discrepancy())
    # Malformed output -> UNKNOWN/MANUAL_REVIEW -> escalated; no exception.
    assert inv.resolution.recommended_action == RecommendedAction.MANUAL_REVIEW
    assert inv.escalated is True


def test_cli_reads_report_from_stdin(monkeypatch, capsys):
    from pathlib import Path

    from recon_agent import cli

    report_json = (Path(__file__).parent / "fixtures" / "example-report.json").read_text()
    monkeypatch.setattr("sys.stdin", io.StringIO(report_json))
    rc = cli.main(["-", "--format", "json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"source_report_id"' in out
