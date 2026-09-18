"""Verify the real Claude adapter parses SDK responses correctly — without network.

We inject a fake object shaped like the Anthropic client so AnthropicClient.complete
can be exercised deterministically. This guards the response-parsing and
request-shaping logic (model, adaptive thinking, tool_use extraction) that the
offline path never touches.
"""

from recon_agent.agent import DisputeAgent
from recon_agent.knowledge import KnowledgeBase
from recon_agent.llm import AnthropicClient
from recon_agent.models import Discrepancy, Money, RecommendedAction, RootCause, Severity


class FakeBlock:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def model_dump(self, exclude_none=False):
        return dict(self.__dict__)


class FakeResponse:
    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason


class FakeMessages:
    def __init__(self, response):
        self._response = response
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return self._response


class FakeAnthropic:
    def __init__(self, response):
        self.messages = FakeMessages(response)


def test_complete_parses_tool_use():
    response = FakeResponse(
        content=[
            FakeBlock(type="text", text="Let me gather evidence."),
            FakeBlock(type="tool_use", id="toolu_1", name="get_events", input={"reference": "TXN-1"}),
        ],
        stop_reason="tool_use",
    )
    fake = FakeAnthropic(response)
    llm = AnthropicClient(client=fake)

    result = llm.complete(system="sys", messages=[{"role": "user", "content": "hi"}], tools=[])

    assert result.stop_reason == "tool_use"
    assert result.text == "Let me gather evidence."
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call.name == "get_events" and call.input == {"reference": "TXN-1"}
    # Assistant content is preserved verbatim for the next turn.
    assert result.assistant_content[1]["name"] == "get_events"


def test_complete_sends_model_and_adaptive_thinking():
    response = FakeResponse(content=[FakeBlock(type="text", text="done")], stop_reason="end_turn")
    fake = FakeAnthropic(response)
    llm = AnthropicClient(model="claude-opus-4-8", client=fake)

    result = llm.complete(system="sys", messages=[{"role": "user", "content": "hi"}], tools=[])

    assert result.stop_reason == "end_turn"
    assert result.tool_calls == []
    sent = fake.messages.last_kwargs
    assert sent["model"] == "claude-opus-4-8"
    assert sent["thinking"] == {"type": "adaptive"}
    assert sent["max_tokens"] == 8192
    # System prompt is sent as a cacheable block (stable across loop turns).
    assert sent["system"][0]["text"] == "sys"
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}


class ScriptedMessages:
    """Returns pre-scripted responses in order; records every request."""

    def __init__(self, responses):
        self._responses = responses
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses[len(self.calls) - 1]


class ScriptedAnthropic:
    def __init__(self, responses):
        self.messages = ScriptedMessages(responses)


def test_full_agent_loop_over_real_client_multiturn():
    """Drive the whole investigate→verify loop through AnthropicClient with a
    scripted SDK client — covering the multi-turn thinking-block round-trip and
    tool_result pairing the single-turn mock never exercises."""
    investigate_turn1 = FakeResponse(
        content=[
            FakeBlock(type="thinking", thinking="Let me check the events.", signature="sig-abc"),
            FakeBlock(type="tool_use", id="toolu_1", name="get_events", input={"reference": "TXN-1005"}),
        ],
        stop_reason="tool_use",
    )
    investigate_turn2 = FakeResponse(
        content=[FakeBlock(type="tool_use", id="toolu_2", name="submit_resolution", input={
            "root_cause": RootCause.FEE_SCHEDULE_DRIFT.value,
            "confidence": 0.9,
            "recommended_action": RecommendedAction.UPDATE_FEE_RECORD.value,
            "rationale": "fee schedule changed",
        })],
        stop_reason="tool_use",
    )
    verify_turn1 = FakeResponse(
        content=[FakeBlock(type="tool_use", id="toolu_3", name="submit_verification",
                           input={"approved": True, "reason": "evidence supports it"})],
        stop_reason="tool_use",
    )
    sdk = ScriptedAnthropic([investigate_turn1, investigate_turn2, verify_turn1])
    agent = DisputeAgent(llm=AnthropicClient(client=sdk), knowledge=KnowledgeBase.default())

    disc = Discrepancy(
        id="d", type="FEE_MISMATCH", severity=Severity.LOW, match_key="TXN-1005",
        monetary_impact=Money(amount_minor=50, currency="USD"), detail="fee",
    )
    inv = agent.investigate(disc)

    assert inv.resolution.root_cause == RootCause.FEE_SCHEDULE_DRIFT
    assert inv.verification.approved is True
    assert inv.escalated is False
    assert any("get_events" in e for e in inv.evidence)

    # The thinking block was echoed back verbatim on turn 2 (API requires the
    # signed block to be replayed unchanged).
    turn2_messages = sdk.messages.calls[1]["messages"]
    assistant_turn = turn2_messages[1]
    assert assistant_turn["role"] == "assistant"
    thinking_blocks = [b for b in assistant_turn["content"] if b.get("type") == "thinking"]
    assert thinking_blocks and thinking_blocks[0]["signature"] == "sig-abc"
