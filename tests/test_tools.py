"""Tests for the tool registry and knowledge base."""

from recon_agent.knowledge import KnowledgeBase
from recon_agent.tools import (
    SUBMIT_RESOLUTION,
    SUBMIT_VERIFICATION,
    ToolRegistry,
    investigation_tools,
)


def test_knowledge_lookups_known_key():
    kb = KnowledgeBase.default()
    psp = kb.get_psp_transaction("TXN-1007")
    assert psp["found"] is True
    events = kb.get_events("TXN-1007")["events"]
    assert any(e["type"] == "webhook_delivery" for e in events)


def test_knowledge_unknown_key_returns_not_found():
    kb = KnowledgeBase.default()
    assert kb.get_ledger_entry("NOPE")["found"] is False
    assert kb.get_events("NOPE")["events"] == []


def test_registry_executes_investigation_tool():
    kb = KnowledgeBase.default()
    registry = ToolRegistry(investigation_tools(kb) + [SUBMIT_RESOLUTION])
    out = registry.execute("get_psp_transaction", {"reference": "TXN-1005"})
    assert out["found"] is True
    assert out["fee_minor"] == 500


def test_registry_unknown_tool():
    registry = ToolRegistry(investigation_tools(KnowledgeBase.default()))
    assert "error" in registry.execute("nonexistent", {})


def test_terminal_tools_are_not_executed():
    registry = ToolRegistry(investigation_tools(KnowledgeBase.default()) + [SUBMIT_RESOLUTION])
    out = registry.execute("submit_resolution", {"root_cause": "TIMING_LAG"})
    assert "error" in out  # terminal tools are captured by the loop, not executed here
    assert SUBMIT_RESOLUTION.is_terminal
    assert SUBMIT_VERIFICATION.is_terminal


def test_tool_handler_exception_is_surfaced():
    kb = KnowledgeBase.default()
    registry = ToolRegistry(investigation_tools(kb))
    # Missing required "reference" key -> handler raises KeyError -> surfaced as error.
    out = registry.execute("get_ledger_entry", {})
    assert "error" in out


def test_tool_definitions_have_strict_schema():
    registry = ToolRegistry(investigation_tools(KnowledgeBase.default()) + [SUBMIT_RESOLUTION])
    for d in registry.definitions():
        assert d["input_schema"]["additionalProperties"] is False
        assert "required" in d["input_schema"]
