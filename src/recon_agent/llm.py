"""The LLM boundary.

The agent talks to *an* LLM through the small :class:`LLMClient` protocol —
one method, ``complete(system, messages, tools) -> LLMResponse``. Two
implementations satisfy it:

* :class:`AnthropicClient` — the real agent, backed by Claude via the Anthropic
  SDK. It runs a single model turn; the agent loop in :mod:`recon_agent.agent`
  drives the request → tool-execute → continue cycle.
* :class:`HeuristicClient` — a deterministic, offline stand-in that plays the
  same role a language model would (gather evidence with the tools, then submit a
  structured verdict), decided by rules instead of a model. It needs no API key,
  which is what lets the whole system run in CI and be unit-tested end to end.

Keeping the boundary this thin is deliberate: the agent logic (loop control,
verification, escalation) is identical regardless of which brain is behind it, so
the tests exercise the real control flow.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .models import RecommendedAction, RootCause

DEFAULT_MODEL = "claude-opus-4-8"


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class LLMResponse:
    stop_reason: str  # "tool_use" | "end_turn"
    tool_calls: list[ToolCall] = field(default_factory=list)
    assistant_content: list[dict[str, Any]] = field(default_factory=list)
    text: str = ""


@runtime_checkable
class LLMClient(Protocol):
    def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LLMResponse: ...


# --------------------------------------------------------------------------- #
# Real client — Claude via the Anthropic SDK
# --------------------------------------------------------------------------- #
class AnthropicClient:
    """Backs the agent with Claude. Requires ANTHROPIC_API_KEY in the environment.

    A single ``complete`` call is one model turn. Adaptive thinking is enabled so
    Claude can reason before acting; the full response content (including any
    thinking blocks) is returned verbatim so the agent loop can append it to the
    conversation unchanged, as the API requires.
    """

    def __init__(self, model: str = DEFAULT_MODEL, max_tokens: int = 4096, client: Any = None):
        if client is None:
            import anthropic  # imported lazily so offline use needs no dependency at import time

            client = anthropic.Anthropic()
        self._client = client
        self._model = model
        self._max_tokens = max_tokens

    def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LLMResponse:
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            thinking={"type": "adaptive"},
            system=system,
            tools=tools,
            messages=messages,
        )
        assistant_content = [_block_to_dict(b) for b in resp.content]
        tool_calls = [
            ToolCall(id=b.id, name=b.name, input=dict(b.input))
            for b in resp.content
            if getattr(b, "type", None) == "tool_use"
        ]
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text")
        return LLMResponse(
            stop_reason=resp.stop_reason or "end_turn",
            tool_calls=tool_calls,
            assistant_content=assistant_content,
            text=text,
        )


def _block_to_dict(block: Any) -> dict[str, Any]:
    if hasattr(block, "model_dump"):
        return block.model_dump(exclude_none=True)
    return dict(block)


# --------------------------------------------------------------------------- #
# Offline client — deterministic rule-based stand-in
# --------------------------------------------------------------------------- #
_EVIDENCE_TOOLS = ("get_psp_transaction", "get_ledger_entry", "get_events")


class HeuristicClient:
    """A deterministic agent brain: gather the standard evidence, then classify by rule.

    It mimics how a model would drive the loop — first a turn that calls the
    evidence tools, then a turn that submits a structured verdict — but the verdict
    is computed from explicit rules, so behavior is fully reproducible. It reads
    only what a real model would see (the conversation and returned tool results),
    never the knowledge base directly.
    """

    def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LLMResponse:
        tool_names = {t["name"] for t in tools}
        next_id = _tool_use_count(messages) + 1

        if "submit_verification" in tool_names:
            return self._verify(messages, next_id)
        return self._investigate(messages, next_id)

    # -- investigator ------------------------------------------------------- #
    def _investigate(self, messages: list[dict[str, Any]], next_id: int) -> LLMResponse:
        if not _has_issued(messages, _EVIDENCE_TOOLS):
            disc = _first_json(messages)
            reference = disc.get("match_key", "")
            calls = [
                ToolCall(id=f"toolu_{next_id + i}", name=name, input={"reference": reference})
                for i, name in enumerate(_EVIDENCE_TOOLS)
            ]
            return LLMResponse(
                stop_reason="tool_use",
                tool_calls=calls,
                assistant_content=[{"type": "text", "text": "Gathering evidence from the systems of record."}]
                + [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in calls],
            )

        disc = _first_json(messages)
        evidence = _collect_evidence(messages)
        resolution = _classify(disc, evidence)
        call = ToolCall(id=f"toolu_{next_id}", name="submit_resolution", input=resolution)
        return LLMResponse(
            stop_reason="tool_use",
            tool_calls=[call],
            assistant_content=[{"type": "tool_use", "id": call.id, "name": call.name, "input": call.input}],
        )

    # -- verifier ----------------------------------------------------------- #
    def _verify(self, messages: list[dict[str, Any]], next_id: int) -> LLMResponse:
        ctx = _first_json(messages)
        resolution = ctx.get("resolution", {})
        root_cause = resolution.get("root_cause", RootCause.UNKNOWN.value)
        confidence = float(resolution.get("confidence", 0.0))
        action = resolution.get("recommended_action", RecommendedAction.MANUAL_REVIEW.value)
        has_evidence = bool(ctx.get("evidence"))

        approved = (
            root_cause != RootCause.UNKNOWN.value
            and confidence >= 0.6
            and has_evidence
            and action != RecommendedAction.MANUAL_REVIEW.value
        )
        reason = (
            f"Evidence supports root cause {root_cause} with confidence {confidence:.2f}."
            if approved
            else f"Insufficient support: root_cause={root_cause}, confidence={confidence:.2f}; routing to human review."
        )
        call = ToolCall(
            id=f"toolu_{next_id}",
            name="submit_verification",
            input={"approved": approved, "reason": reason},
        )
        return LLMResponse(
            stop_reason="tool_use",
            tool_calls=[call],
            assistant_content=[{"type": "tool_use", "id": call.id, "name": call.name, "input": call.input}],
        )


# --- rule engine used by the offline client -------------------------------- #
def _classify(disc: dict[str, Any], evidence: dict[str, dict[str, Any]]) -> dict[str, Any]:
    dtype = disc.get("type", "")
    events = (evidence.get("get_events") or {}).get("events", [])
    event_types = {e.get("type") for e in events}

    def result(root: RootCause, conf: float, action: RecommendedAction, why: str) -> dict[str, Any]:
        return {
            "root_cause": root.value,
            "confidence": conf,
            "recommended_action": action.value,
            "rationale": why,
        }

    if dtype in ("DUPLICATE_IN_PSP", "DUPLICATE_IN_LEDGER") and "webhook_delivery" in event_types:
        return result(RootCause.DUPLICATE_WEBHOOK, 0.9, RecommendedAction.DEDUPE_LEDGER_ENTRY,
                      "A retried webhook with the same event_id produced a duplicate settlement row.")
    if dtype == "MISSING_IN_LEDGER" and "ledger_booking_schedule" in event_types:
        return result(RootCause.TIMING_LAG, 0.85, RecommendedAction.NO_ACTION_TIMING,
                      "PSP settled; the ledger booking job runs on T+1 and has not yet materialized the entry.")
    if dtype == "MISSING_IN_PSP" and "psp_auth_lifecycle" in event_types:
        return result(RootCause.MISSING_CAPTURE, 0.8, RecommendedAction.REVERSE_LEDGER_ENTRY,
                      "The PSP authorization expired without capture; the ledger booked revenue that never settled.")
    if dtype == "FEE_MISMATCH" and "fee_schedule" in event_types:
        return result(RootCause.FEE_SCHEDULE_DRIFT, 0.9, RecommendedAction.UPDATE_FEE_RECORD,
                      "A fee-schedule change took effect; the ledger used the prior fee version.")
    if dtype == "AMOUNT_MISMATCH" and "fx_rate" in event_types:
        return result(RootCause.ROUNDING_OR_FX, 0.7, RecommendedAction.ADJUST_LEDGER_AMOUNT,
                      "FX rate drifted between authorization and settlement, explaining the amount delta.")
    if dtype == "STATUS_MISMATCH" and "refund_attempt" in event_types:
        return result(RootCause.FAILED_REFUND, 0.6, RecommendedAction.REISSUE_REFUND,
                      "The refund was booked in the ledger but the PSP refund attempt failed.")

    return result(RootCause.UNKNOWN, 0.3, RecommendedAction.MANUAL_REVIEW,
                  "Available evidence does not clearly explain the discrepancy.")


# --- message inspection helpers --------------------------------------------- #
def _tool_use_count(messages: list[dict[str, Any]]) -> int:
    n = 0
    for m in messages:
        for block in _blocks(m):
            if block.get("type") == "tool_use":
                n += 1
    return n


def _has_issued(messages: list[dict[str, Any]], names: tuple[str, ...]) -> bool:
    for m in messages:
        for block in _blocks(m):
            if block.get("type") == "tool_use" and block.get("name") in names:
                return True
    return False


def _collect_evidence(messages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Map tool_use_id -> tool name, then gather each tool_result's parsed payload."""
    id_to_name: dict[str, str] = {}
    for m in messages:
        for block in _blocks(m):
            if block.get("type") == "tool_use":
                id_to_name[block.get("id", "")] = block.get("name", "")

    evidence: dict[str, dict[str, Any]] = {}
    for m in messages:
        for block in _blocks(m):
            if block.get("type") == "tool_result":
                name = id_to_name.get(block.get("tool_use_id", ""))
                if not name:
                    continue
                payload = _parse_result_content(block.get("content"))
                if payload is not None:
                    evidence[name] = payload
    return evidence


def _parse_result_content(content: Any) -> dict[str, Any] | None:
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return None
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                try:
                    return json.loads(part.get("text", ""))
                except json.JSONDecodeError:
                    return None
    return None


def _blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content")
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _first_json(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract the first balanced JSON object found in any user text block."""
    for m in messages:
        content = m.get("content")
        texts: list[str] = []
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            texts.extend(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
        for text in texts:
            obj = _extract_json_object(text)
            if obj is not None:
                return obj
    return {}


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Return the first top-level JSON object embedded in ``text``.

    Uses ``JSONDecoder.raw_decode`` rather than brace counting so that braces
    appearing *inside* string values (e.g. an evidence line like
    ``get_events(...) -> {"found": true}``) do not confuse the scan.
    """
    decoder = json.JSONDecoder()
    idx = text.find("{")
    while idx != -1:
        try:
            obj, _ = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            idx = text.find("{", idx + 1)
            continue
        if isinstance(obj, dict):
            return obj
        idx = text.find("{", idx + 1)
    return None
