"""Tool definitions and the registry that executes them.

Two kinds of tool:

* **Investigation tools** (``get_ledger_entry``, ``get_psp_transaction``,
  ``get_events``) read the systems of record via a :class:`KnowledgeBase`. The
  agent calls these to gather evidence.
* **Terminal tools** (``submit_resolution``, ``submit_verification``) have no
  handler — when the model calls one, the agent loop captures its input as the
  structured result and stops. Using a tool call (rather than parsing free-text
  JSON) to finalize guarantees a schema-valid payload.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .knowledge import KnowledgeBase
from .models import RecommendedAction, RootCause


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    strict: bool = False

    @property
    def is_terminal(self) -> bool:
        return self.handler is None

    def definition(self) -> dict[str, Any]:
        """The Anthropic tool definition.

        Terminal tools set ``strict`` so the model's finalizing tool input is
        guaranteed to validate against the schema (valid enum values, all
        required fields, no extras) — no defensive parsing of model output.
        """
        d: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
        if self.strict:
            d["strict"] = True
        return d


class ToolRegistry:
    def __init__(self, tools: list[Tool]):
        self._by_name = {t.name: t for t in tools}

    def definitions(self) -> list[dict[str, Any]]:
        return [t.definition() for t in self._by_name.values()]

    def get(self, name: str) -> Tool | None:
        return self._by_name.get(name)

    def execute(self, name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        tool = self._by_name.get(name)
        if tool is None:
            return {"error": f"unknown tool {name!r}"}
        if tool.handler is None:
            return {"error": f"{name!r} is a terminal tool and is not executed here"}
        try:
            return tool.handler(tool_input)
        except Exception as exc:  # surface tool failure to the model, don't crash
            return {"error": f"{type(exc).__name__}: {exc}"}


# --------------------------------------------------------------------------- #
# Investigation tools
# --------------------------------------------------------------------------- #
_REFERENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "reference": {
            "type": "string",
            "description": "The discrepancy's match_key (the shared transaction reference).",
        }
    },
    "required": ["reference"],
    "additionalProperties": False,
}


def investigation_tools(kb: KnowledgeBase) -> list[Tool]:
    return [
        Tool(
            name="get_ledger_entry",
            description="Fetch the internal ledger entry for a transaction reference (status, amount, booking metadata).",
            input_schema=_REFERENCE_SCHEMA,
            handler=lambda i: kb.get_ledger_entry(i["reference"]),
        ),
        Tool(
            name="get_psp_transaction",
            description="Fetch the PSP/gateway view of a transaction reference (status, amount, fee, capture/auth lifecycle).",
            input_schema=_REFERENCE_SCHEMA,
            handler=lambda i: kb.get_psp_transaction(i["reference"]),
        ),
        Tool(
            name="get_events",
            description="Fetch the event trail for a reference: webhook deliveries, refund attempts, fee-schedule changes, FX rates, auth lifecycle. Use this to explain *why* the two sides differ.",
            input_schema=_REFERENCE_SCHEMA,
            handler=lambda i: kb.get_events(i["reference"]),
        ),
    ]


# --------------------------------------------------------------------------- #
# Terminal tools
# --------------------------------------------------------------------------- #
def _enum_values(enum_cls) -> list[str]:
    return [e.value for e in enum_cls]


SUBMIT_RESOLUTION = Tool(
    name="submit_resolution",
    description=(
        "Submit your final classification for this discrepancy. Call this exactly "
        "once, only after gathering enough evidence. If the evidence is ambiguous "
        "or insufficient, classify root_cause=UNKNOWN with a low confidence and "
        "recommended_action=MANUAL_REVIEW rather than guessing."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "root_cause": {"type": "string", "enum": _enum_values(RootCause)},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "recommended_action": {"type": "string", "enum": _enum_values(RecommendedAction)},
            "rationale": {
                "type": "string",
                "description": "One or two sentences citing the specific evidence that supports this conclusion.",
            },
        },
        "required": ["root_cause", "confidence", "recommended_action", "rationale"],
        "additionalProperties": False,
    },
    strict=True,
)

SUBMIT_VERIFICATION = Tool(
    name="submit_verification",
    description=(
        "Submit your verdict on whether the proposed resolution is supported by the "
        "evidence. Approve only if the evidence clearly backs the root cause and the "
        "recommended action is appropriate for it."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "approved": {"type": "boolean"},
            "reason": {"type": "string"},
        },
        "required": ["approved", "reason"],
        "additionalProperties": False,
    },
    strict=True,
)
