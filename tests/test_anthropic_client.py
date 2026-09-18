"""Verify the real Claude adapter parses SDK responses correctly — without network.

We inject a fake object shaped like the Anthropic client so AnthropicClient.complete
can be exercised deterministically. This guards the response-parsing and
request-shaping logic (model, adaptive thinking, tool_use extraction) that the
offline path never touches.
"""

from recon_agent.llm import AnthropicClient


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
    assert sent["system"] == "sys"
