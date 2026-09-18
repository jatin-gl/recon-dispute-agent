# Agent Design

This document explains *why* the agent is built the way it is — the design
decisions a reviewer would want to interrogate.

## Why an agent (and not a classifier)?

Reconciliation triage is a good fit for an agentic loop, by the usual criteria:

- **Multi-step and hard to fully specify up front.** Explaining a discrepancy
  means pulling the PSP view, the ledger view, and the event trail, then reasoning
  over what you find. The right next lookup depends on the last one.
- **Value justifies the cost.** A correct root cause turns a human's 15-minute
  investigation into a reviewed suggestion.
- **Errors are catchable.** Every proposed resolution is checked by a verifier and
  can be escalated — nothing is auto-applied blindly.

The unit of work is one discrepancy. The agent runs the same loop per finding, so
throughput scales and one hard case can't stall the batch.

## Tool surface

The agent is given three read-only investigation tools and one terminal tool:

| Tool | Purpose |
|---|---|
| `get_psp_transaction` | The PSP/gateway view: status, amount, fee, capture/auth lifecycle |
| `get_ledger_entry` | The internal ledger view |
| `get_events` | The event trail — webhook deliveries, refund attempts, fee-schedule changes, FX rates. This is usually what *explains* the discrepancy. |
| `submit_resolution` | Terminal: the model's structured verdict |

Tools are **read-only by design.** The agent proposes an action
(`recommended_action`); it never mutates a system of record. Applying a
correction is a separate, gated step — exactly the reversibility boundary you
want for money movement.

Finalizing via a **tool call** (`submit_resolution`) rather than free-text JSON
means the payload is schema-validated by construction — no brittle parsing of the
model's prose. The terminal tools are declared `strict`, so the model's finalizing
input is guaranteed to satisfy the schema (valid enum values, all required
fields). As a belt-and-suspenders measure the agent also **fails closed**: if any
payload nonetheless fails validation, it degrades to `UNKNOWN` / `MANUAL_REVIEW`
and escalates rather than raising.

## The verifier loop

After the investigator proposes a resolution, a second, independent loop runs a
**skeptical verifier** with its own system prompt. It sees the discrepancy, the
proposed resolution, and the evidence trail, and may re-fetch evidence. It
approves only if the evidence clearly supports the root cause and the action fits.

This is a deliberate check on the failure mode that matters most here — a
*plausible but wrong* root cause. A single model pass is prone to it; an
adversarial second opinion catches a good fraction. The verifier is a separate
`LLMClient` call, so in production you could even run it on a different model.

## Escalation policy

A finding is escalated to human review when **any** of:

- the verifier rejects the resolution,
- the recommended action is `MANUAL_REVIEW`, or
- the root cause is `UNKNOWN`.

The prompts explicitly instruct the model to classify `UNKNOWN` / `MANUAL_REVIEW`
when evidence is thin **rather than guess**. In payments, a wrong auto-correction
is worse than a human review — so the design biases toward escalation under
uncertainty. `test_unknown_transaction_escalates_to_manual_review` locks this in.

## The offline brain

`HeuristicClient` is a deterministic stand-in for the model. It follows the same
protocol — gather evidence, then submit a structured verdict — but decides by
explicit rules over the event trail. It exists for three reasons:

1. **Testability.** The whole loop, verifier, and escalation logic can be tested
   end to end with no network and no flakiness.
2. **CI and demos run with no API key.** `make run-example` works out of the box.
3. **A cost/latency floor.** For the clear-cut cases the rules already handle
   well, you can skip the model entirely.

The real value of the Claude-backed client shows on the long tail — messy,
multi-hop, or ambiguous cases the rules can't enumerate — where genuine reasoning
over the evidence earns its cost.

## Root-cause taxonomy

A closed set (`models.RootCause`) keyed to how payments actually break:
`TIMING_LAG`, `DUPLICATE_WEBHOOK`, `FEE_SCHEDULE_DRIFT`, `ROUNDING_OR_FX`,
`UNRECORDED_REFUND`, `FAILED_REFUND`, `MISSING_CAPTURE`, and `UNKNOWN`. Each maps
to a least-invasive recommended action. Keeping it closed makes the output
routable by downstream automation instead of free-form text.

## Prompting notes

- The investigator prompt insists conclusions cite retrieved evidence, prefers the
  least invasive correct action, and mandates `UNKNOWN` over guessing.
- The verifier prompt is explicitly adversarial — "when in doubt, reject."
- Model default is `claude-opus-4-8` with adaptive thinking, so the model decides
  how much to reason per case; the full response (including thinking) is echoed
  back into the loop unchanged, as the API requires.
