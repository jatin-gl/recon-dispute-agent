"""The dispute agent: an evidence-gathering investigation loop plus a verifier.

For each discrepancy the agent:

1. runs a tool-use loop (``investigate``) in which the LLM gathers evidence and
   ends by calling ``submit_resolution``;
2. runs a second, independent tool-use loop (``verify``) in which a skeptical
   reviewer approves or rejects the proposed resolution;
3. escalates to human review when verification fails, confidence is low, or the
   root cause is UNKNOWN.

The loop is provider-agnostic — it drives whatever :class:`LLMClient` it is
given, so the identical control flow runs against Claude or the offline brain.
"""

from __future__ import annotations

import json
from typing import Any

from .knowledge import KnowledgeBase
from .llm import LLMClient
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
from .prompts import (
    INVESTIGATOR_SYSTEM,
    VERIFIER_SYSTEM,
    investigator_task,
    verifier_task,
)
from .tools import (
    SUBMIT_RESOLUTION,
    SUBMIT_VERIFICATION,
    ToolRegistry,
    investigation_tools,
)


class DisputeAgent:
    def __init__(
        self,
        llm: LLMClient,
        knowledge: KnowledgeBase | None = None,
        verifier_llm: LLMClient | None = None,
        max_steps: int = 6,
    ):
        self._llm = llm
        self._verifier_llm = verifier_llm or llm
        self._kb = knowledge or KnowledgeBase.default()
        self._max_steps = max_steps

    def run(self, report: Report) -> InvestigationReport:
        return InvestigationReport(
            source_report_id=report.report_id,
            contract_version=report.contract_version,
            investigations=[self.investigate(d) for d in report.discrepancies],
        )

    def investigate(self, discrepancy: Discrepancy) -> Investigation:
        registry = ToolRegistry(investigation_tools(self._kb) + [SUBMIT_RESOLUTION])
        content = [{"type": "text", "text": investigator_task(discrepancy.model_dump_json(indent=2))}]

        payload, evidence, steps = self._run_loop(
            self._llm, INVESTIGATOR_SYSTEM, content, registry, "submit_resolution"
        )
        resolution = _resolution_from(payload)
        verification = self.verify(discrepancy, resolution, evidence)

        escalated = (
            not verification.approved
            or resolution.recommended_action == RecommendedAction.MANUAL_REVIEW
            or resolution.root_cause == RootCause.UNKNOWN
        )
        return Investigation(
            discrepancy_id=discrepancy.id,
            match_key=discrepancy.match_key,
            discrepancy_type=discrepancy.type.value,
            severity=discrepancy.severity.value,
            resolution=resolution,
            verification=verification,
            evidence=evidence,
            escalated=escalated,
            steps=steps,
        )

    def verify(
        self, discrepancy: Discrepancy, resolution: Resolution, evidence: list[str]
    ) -> VerificationResult:
        registry = ToolRegistry(investigation_tools(self._kb) + [SUBMIT_VERIFICATION])
        context = {
            "discrepancy": json.loads(discrepancy.model_dump_json()),
            "resolution": json.loads(resolution.model_dump_json()),
            "evidence": evidence,
        }
        content = [{"type": "text", "text": verifier_task(json.dumps(context, indent=2))}]

        payload, _, _ = self._run_loop(
            self._verifier_llm, VERIFIER_SYSTEM, content, registry, "submit_verification"
        )
        if payload is None:
            return VerificationResult(approved=False, reason="verifier did not return a verdict")
        return VerificationResult(**payload)

    # ------------------------------------------------------------------ #
    def _run_loop(
        self,
        llm: LLMClient,
        system: str,
        user_content: list[dict[str, Any]],
        registry: ToolRegistry,
        terminal_tool: str,
    ) -> tuple[dict[str, Any] | None, list[str], int]:
        """Drive one request→tool→continue loop until the terminal tool is called.

        Returns the terminal tool's input payload (or None if the loop ended
        without it), the human-readable evidence trail, and the step count.
        """
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_content}]
        evidence: list[str] = []
        tool_defs = registry.definitions()

        for step in range(1, self._max_steps + 1):
            resp = llm.complete(system, messages, tool_defs)
            messages.append({"role": "assistant", "content": resp.assistant_content})

            if resp.stop_reason != "tool_use" or not resp.tool_calls:
                return None, evidence, step  # model stopped without finalizing

            tool_results: list[dict[str, Any]] = []
            terminal_payload: dict[str, Any] | None = None
            for call in resp.tool_calls:
                if call.name == terminal_tool:
                    terminal_payload = call.input
                    tool_results.append(_result_block(call.id, {"ok": True}))
                    continue
                out = registry.execute(call.name, call.input)
                evidence.append(f"{call.name}({_compact(call.input)}) -> {_compact(out)}")
                tool_results.append(_result_block(call.id, out))

            messages.append({"role": "user", "content": tool_results})
            if terminal_payload is not None:
                return terminal_payload, evidence, step

        return None, evidence, self._max_steps


def _resolution_from(payload: dict[str, Any] | None) -> Resolution:
    if payload is None:
        return Resolution(
            root_cause=RootCause.UNKNOWN,
            confidence=0.0,
            recommended_action=RecommendedAction.MANUAL_REVIEW,
            rationale="Investigation did not converge on a resolution within the step budget.",
        )
    return Resolution(**payload)


def _result_block(tool_use_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": json.dumps(payload)}


def _compact(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"))
