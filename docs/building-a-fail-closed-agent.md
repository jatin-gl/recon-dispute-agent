# Building a fail-closed AI agent for payments reconciliation

Reconciliation is where payments quietly break. A settlement lands a cent short,
a refund is booked in the ledger but never actually processed at the gateway, a
webhook double-fires and a charge settles twice. A reconciliation *engine* can
tell you *what* doesn't line up between a payment provider's settlement file and
your internal ledger. It can't tell you *why*, or what to do about it — that's
the judgment work a payments-operations analyst does by hand, one discrepancy at
a time: pull the provider's view, pull the ledger's view, look at the event
trail, form a theory, and decide whether to fix it or escalate.

That investigation is a good fit for an LLM agent, and it's also a good fit for
the failure modes that make people (rightly) nervous about agents touching money.
This is a write-up of how I built one — `recon-dispute-agent` — and, more
importantly, the design decisions that keep it trustworthy: an independent
verifier, a fail-closed escalation policy, and a provider-agnostic loop that lets
me test the *real* control flow deterministically without spending a token.

## Should this even be an agent?

Not every LLM feature should be an agent. Before reaching for a loop, four things
have to be true:

- **The task is multi-step and hard to fully specify up front.** Explaining a
  discrepancy means gathering evidence where the *next* lookup depends on what the
  last one returned. You can't write that as a fixed pipeline.
- **The value justifies the cost.** A correct root cause turns a 15-minute manual
  investigation into a reviewed one-line suggestion. That's worth a few model
  calls.
- **Errors are catchable.** Every proposal is checked and can be escalated;
  nothing is auto-applied blindly.
- **The model is actually capable at it.** Root-cause classification over
  structured evidence is squarely in scope.

If any of those is "no," you should stay at a simpler tier — a single call, or a
hand-written workflow. Here, all four hold, so an agent earns its place. The unit
of work is one discrepancy; the same loop runs per finding, so throughput scales
and one hard case can't stall the batch.

## The shape of the system

```
engine report (JSON) ─▶ DisputeAgent ─▶ for each discrepancy:
                                          investigate (tool loop) ─▶ Resolution
                                          verify      (tool loop) ─▶ approve / reject
                                          escalate if unproven
                                        ─▶ InvestigationReport
```

For each discrepancy the agent runs **two independent loops**:

1. **Investigation.** The model is given the discrepancy and a set of read-only
   tools — `get_psp_transaction`, `get_ledger_entry`, `get_events` — that read
   the systems of record. It gathers evidence, reasons to a root cause, and ends
   by calling a terminal `submit_resolution` tool with a structured verdict:
   root cause, confidence, recommended action, and a rationale that must cite the
   evidence it actually retrieved.
2. **Verification.** A second loop, with its own skeptical system prompt, is given
   the discrepancy, the proposed resolution, and the evidence trail. It approves
   the resolution only if the evidence clearly supports the root cause and the
   action fits — otherwise it rejects.

Anything the verifier rejects, or that the investigator classified as `UNKNOWN` /
`MANUAL_REVIEW`, is **escalated to a human**. Nothing is auto-applied.

## Design decision 1: read-only tools, and a tool call to finalize

The agent's investigation tools are **read-only by design.** They fetch the
provider view, the ledger view, and the event trail. The agent proposes an action
(`recommended_action`) — it never mutates a system of record. Applying a
correction is a separate, gated step. This is the reversibility boundary you want
around money movement: the agent can *think*, but the blast radius of a wrong
thought is a suggestion, not a double refund.

The agent also finalizes by **calling a tool** (`submit_resolution`) rather than
emitting free-text JSON. This matters more than it sounds. The terminal tools are
declared `strict`, so the model's finalizing input is guaranteed to satisfy the
schema — valid enum values, all required fields, no extras. There's no brittle
parsing of the model's prose, and no "the model wrote almost-valid JSON" class of
bug.

## Design decision 2: an adversarial verifier

A single model pass is prone to a specific failure: a **plausible but wrong** root
cause. The model finds *a* story that fits and commits to it. In payments, a
confident wrong answer is worse than no answer.

So the second loop is deliberately adversarial. Its prompt tells it to approve
only when the evidence clearly backs the conclusion and to **reject when in
doubt** — "a wrongly auto-applied correction is worse than a human review." It's a
separate `LLMClient` call, so in production you could even run it on a different
model than the investigator. A second, skeptical opinion catches a meaningful
fraction of the plausible-but-wrong cases that a single pass would wave through.

## Design decision 3: fail closed, everywhere

The whole system is built to **degrade toward escalation, never toward a confident
guess or a crash.** Concretely:

- If the investigation loop never calls `submit_resolution` within its step
  budget, the result degrades to `UNKNOWN` / `MANUAL_REVIEW`.
- If the model returns a payload that somehow fails validation, it degrades the
  same way instead of raising.
- If the verifier returns a malformed verdict, it's treated as a rejection.
- The prompts explicitly instruct the model to classify `UNKNOWN` when the
  evidence is thin **rather than guess**.

This is the property I'd defend hardest in review. An agent that occasionally says
"I'm not sure, a human should look at this" is trustworthy. An agent that
occasionally invents a confident wrong reason to reverse a ledger entry is a
liability. Every ambiguous path in the code routes to the first behavior.

The taxonomy reinforces this. Root causes are a **closed set** keyed to how
payments actually break — `TIMING_LAG`, `DUPLICATE_WEBHOOK`, `FEE_SCHEDULE_DRIFT`,
`ROUNDING_OR_FX`, `UNRECORDED_REFUND`, `FAILED_REFUND`, `MISSING_CAPTURE`, and
`UNKNOWN` — each mapped to a least-invasive recommended action. A closed set is
routable by downstream automation; free-text reasons are not.

## Design decision 4: one thin LLM boundary, two implementations

Everything the agent does routes through a single interface:

```python
class LLMClient(Protocol):
    def complete(self, system, messages, tools) -> LLMResponse: ...
```

There are two implementations behind it:

- **`AnthropicClient`** — the real agent, backed by Claude. One `complete` call is
  one model turn (adaptive thinking on); the agent loop drives the request →
  execute tools → continue cycle. The full response content, including thinking
  blocks with their signatures, is round-tripped verbatim so multi-turn tool use
  replays correctly.
- **`HeuristicClient`** — a deterministic, offline stand-in. It plays the *same
  role* a model would — gather the standard evidence with the tools, then submit a
  structured verdict — but decides by explicit rules over the event trail instead
  of by a model. It reads only what a real model would see (the conversation and
  the returned tool results), never the knowledge base directly.

Because the boundary is this thin, **the agent's control flow — the loop, the
verifier, the escalation — is identical regardless of which brain is behind it.**
That has three payoffs:

1. **The tests exercise the real machinery.** CI drives the *actual* loop through
   the offline client: evidence gathering, terminal-tool capture, `tool_result`
   pairing, the verifier pass, escalation. What CI verifies is the same code that
   runs against Claude — not a mock of it.
2. **It runs with no API key.** `make run-example` and the whole test suite work
   offline, deterministically, with zero flakiness and zero cost. That's what
   makes it CI-friendly and demo-friendly.
3. **It's a cost/latency floor.** For the clear-cut cases the rules already handle
   well, you can skip the model entirely.

The real value of Claude shows on the long tail — the messy, multi-hop, ambiguous
cases the rules can't enumerate — where genuine reasoning over the evidence earns
its cost.

## Forward-compatibility as a first-class concern

The agent consumes a versioned data contract from the engine. Contracts evolve — a
newer engine will eventually emit a discrepancy type this version of the agent
doesn't know. The naive behavior (strict enum parsing) would reject the *entire*
report because of one unrecognized value, dropping the findings the agent fully
understands.

So parsing is deliberately lenient in a bounded way: an unrecognized (or missing,
or null) `type` is mapped to an `OTHER` sentinel, with the original string
preserved for the human, and it's pushed toward escalation. One unknown value
never nukes the batch. This is the input-side analog of fail-closed: the system
survives an evolving contract instead of hard-failing on it.

## Testing an agent you can't call

The hardest part of testing an agent is usually that the model is nondeterministic
and costs money. The provider-agnostic boundary mostly dissolves that problem, but
there's still one path the offline brain can't exercise: the *real client's*
response parsing and multi-turn round-trip. I close that gap with a scripted fake
SDK client — canned responses for turn 1 (a thinking block plus a tool call), the
tool result, and turn 2 (the finalizing call) — driven through the full agent so
the assertions cover the real adapter: that the thinking block's signature is
echoed back unchanged on the next turn, that tool results are paired correctly,
and that the loop terminates on the finalizing tool.

Between that and the offline determinism, the suite covers the machinery end to
end without a network call.

## What I'd do next

A few things I deliberately left as follow-ups rather than shipping half-done:

- **Streaming on the real path** for long investigations, so a hard case doesn't
  silently hit the token cap and fall back to manual review.
- **Prompt caching** is already applied to the stable system prefix; the next step
  is measuring cache hit rates under real traffic.
- **A richer verifier** that re-derives evidence independently rather than
  reviewing the investigator's trail, for the highest-stakes findings.

## The takeaway

The interesting part of this project isn't the LLM call — it's the scaffolding
around it that makes an agent safe to point at money: read-only tools with a gated
action step, an adversarial verifier, a fail-closed policy where every ambiguous
path escalates, a forward-compatible input contract, and a provider-agnostic
boundary that makes the real control flow deterministically testable. The model is
the easy part to swap; the trust properties are the design.
