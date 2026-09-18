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


def test_unknown_discrepancy_type_does_not_reject_whole_report(agent, report):
    # A newer engine emits a type this agent doesn't know. The whole report must
    # still parse and every discrepancy must still be investigated.
    import json as _json

    from recon_agent import load_report

    base = _json.loads(report.model_dump_json())
    base["discrepancies"][0]["type"] = "CHARGEBACK_REVERSAL"
    r = load_report(_json.dumps(base))

    assert len(r.discrepancies) == len(report.discrepancies)
    assert r.discrepancies[0].type.value == "OTHER"
    assert r.discrepancies[0].type_label == "CHARGEBACK_REVERSAL"  # original preserved

    result = agent.run(r)
    assert len(result.investigations) == len(report.discrepancies)
    inv0 = result.investigations[0]
    assert inv0.discrepancy_type == "CHARGEBACK_REVERSAL"
    assert inv0.escalated is True  # unrecognized type -> escalate, never a confident guess


def test_unknown_severity_defaults_to_high():
    from recon_agent.models import Discrepancy, Severity

    d = Discrepancy.model_validate({
        "id": "d", "type": "AMOUNT_MISMATCH", "severity": "apocalyptic",
        "match_key": "K", "monetary_impact": {"amount_minor": 1, "currency": "USD"}, "detail": "x",
    })
    assert d.severity == Severity.HIGH


class _VerifierFetchesEvidence:
    """A verifier brain that fetches one piece of evidence before approving."""

    def complete(self, system, messages, tools):
        already = any(
            b.get("type") == "tool_use" and b.get("name") == "get_events"
            for m in messages
            for b in (m.get("content") or [])
            if isinstance(b, dict)
        )
        if not already:
            return LLMResponse(
                stop_reason="tool_use",
                tool_calls=[ToolCall("v1", "get_events", {"reference": "TXN-1005"})],
                assistant_content=[{"type": "tool_use", "id": "v1", "name": "get_events", "input": {"reference": "TXN-1005"}}],
            )
        return LLMResponse(
            stop_reason="tool_use",
            tool_calls=[ToolCall("v2", "submit_verification", {"approved": True, "reason": "re-checked events"})],
            assistant_content=[{"type": "tool_use", "id": "v2", "name": "submit_verification", "input": {"approved": True, "reason": "re-checked events"}}],
        )


def test_verifier_gathered_evidence_is_recorded():
    agent = DisputeAgent(
        llm=HeuristicClient(),
        verifier_llm=_VerifierFetchesEvidence(),
        knowledge=KnowledgeBase.default(),
    )
    inv = agent.investigate(_sample_discrepancy())
    assert any(e.startswith("[verifier]") and "get_events" in e for e in inv.evidence), inv.evidence


def test_null_or_missing_type_is_tolerated():
    from recon_agent.models import Discrepancy, DiscrepancyType

    base = {"id": "d", "severity": "high", "match_key": "K",
            "monetary_impact": {"amount_minor": 1, "currency": "USD"}, "detail": "x"}
    missing = Discrepancy.model_validate(base)  # no "type" key at all
    assert missing.type == DiscrepancyType.OTHER
    null = Discrepancy.model_validate({**base, "type": None})
    assert null.type == DiscrepancyType.OTHER


def test_escalated_count_is_serialized_to_json(agent, report):
    import json as _json

    result = agent.run(report)
    data = _json.loads(result.model_dump_json())
    assert "escalated_count" in data  # exposed to JSON/API consumers, not just the CLI text view
    assert data["escalated_count"] == result.escalated_count


def test_cli_handles_bom_prefixed_file(tmp_path, capsys):
    from pathlib import Path

    from recon_agent import cli

    fixture = Path(__file__).parent / "fixtures" / "example-report.json"
    bom_file = tmp_path / "bom-report.json"
    bom_file.write_bytes(b"\xef\xbb\xbf" + fixture.read_bytes())
    rc = cli.main([str(bom_file), "--format", "json"])
    assert rc == 0  # BOM tolerated, not misread as a filename -> no crash
    assert '"source_report_id"' in capsys.readouterr().out


def test_cli_missing_file_exits_cleanly(capsys):
    from recon_agent import cli

    rc = cli.main(["/nonexistent/report.json"])
    assert rc == 1
    assert "cannot read report" in capsys.readouterr().err


def test_print_utf8_falls_back_on_non_utf8_stdout(monkeypatch):
    import io

    from recon_agent import cli

    class AsciiStdout:
        def __init__(self):
            self.buffer = io.BytesIO()

        def reconfigure(self, **kwargs):
            raise AttributeError  # simulate a stream that can't be reconfigured

        def write(self, s):
            raise UnicodeEncodeError("ascii", s, 0, 1, "ordinal not in range(128)")

        def flush(self):
            pass

    fake = AsciiStdout()
    monkeypatch.setattr("sys.stdout", fake)
    cli._print_utf8("✓ done")  # must not raise
    assert "✓ done".encode() in fake.buffer.getvalue()


def test_cli_reads_report_from_stdin(monkeypatch, capsys):
    from pathlib import Path

    from recon_agent import cli

    report_json = (Path(__file__).parent / "fixtures" / "example-report.json").read_text()
    monkeypatch.setattr("sys.stdin", io.StringIO(report_json))
    rc = cli.main(["-", "--format", "json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"source_report_id"' in out
