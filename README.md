# recon-dispute-agent

[![CI](https://github.com/jatin-gl/recon-dispute-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/jatin-gl/recon-dispute-agent/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Claude](https://img.shields.io/badge/LLM-Claude%20(Anthropic)-d97757)](https://www.anthropic.com/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

An **AI agent that investigates payment reconciliation discrepancies**. It takes
the output of the
[**payment-reconciliation-engine**](https://github.com/jatin-gl/payment-reconciliation-engine)
— a list of transactions that don't line up between a PSP settlement and an
internal ledger — and, for each one, runs a tool-using investigation with Claude:
it pulls the PSP view, the ledger view, and the event trail, reasons to a root
cause, and proposes a resolution. A second **verifier** agent then checks the
conclusion against the evidence, and anything unproven is **escalated to a human**.

> **The story.** The engine answers *"what doesn't reconcile?"* This agent answers
> *"why, and what should we do about it?"* — the judgment work a payments-ops
> analyst does by hand, framed as an agentic workflow with tool use, an
> adversarial verification pass, and a bias toward escalation over guessing.

It runs **fully offline with no API key** (a deterministic brain stands in for the
model), which is how the whole thing is unit-tested end to end — then swaps to
real Claude with a single flag.

---

## What it demonstrates

- **A real agentic loop over custom tools** — evidence gathering, a schema-valid
  terminal tool call to finalize, bounded steps.
- **A verifier / critic pass** — an independent skeptical review of each proposed
  resolution, the standard guard against plausible-but-wrong LLM output.
- **Escalation under uncertainty** — `UNKNOWN` / `MANUAL_REVIEW` instead of a
  confident guess, because a wrong auto-correction in payments is expensive.
- **A clean LLM boundary** — one `complete()` protocol, two implementations
  (Claude and a deterministic offline brain), so identical control flow is tested
  in CI and runs in production.
- **End-to-end integration** with the engine's versioned data contract; money is
  integer minor units the whole way through.

## Quickstart (no API key)

```bash
git clone https://github.com/jatin-gl/recon-dispute-agent
cd recon-dispute-agent
make install
make run-example      # investigates the bundled engine report, offline
```

Output:

```
Investigation of report rpt_… (contract v1.0)
  6 discrepancies investigated, 0 escalated to human review

[high    ] DUPLICATE_IN_PSP   TXN-1007
    root cause : DUPLICATE_WEBHOOK (confidence 0.90)
    action     : DEDUPE_LEDGER_ENTRY  [✓ auto]
    rationale  : A retried webhook with the same event_id produced a duplicate settlement row.
    verified   : True — Evidence supports root cause DUPLICATE_WEBHOOK with confidence 0.90.
    evidence   : 3 tool call(s) over 2 step(s)
[high    ] MISSING_IN_PSP     TXN-1004
    root cause : MISSING_CAPTURE (confidence 0.80)
    action     : REVERSE_LEDGER_ENTRY  [✓ auto]
    rationale  : The PSP authorization expired without capture; the ledger booked revenue that never settled.
    ...
```

## Running against Claude

```bash
export ANTHROPIC_API_KEY=sk-ant-...
recon-agent report.json --mode anthropic
```

`--mode anthropic` swaps the deterministic brain for
[`AnthropicClient`](src/recon_agent/llm.py) (model `claude-opus-4-8`, adaptive
thinking). The loop, tools, verifier, and escalation are byte-for-byte the same —
only the decision-maker changes.

## HTTP service

```bash
make run-server     # uvicorn on :8000, offline mode by default

curl -s -X POST http://localhost:8000/v1/investigate \
  -H 'content-type: application/json' \
  --data @report.json | jq '.investigations[] | {match_key, root_cause: .resolution.root_cause, escalated}'
```

| Method & path | Description |
|---|---|
| `GET /healthz` | Liveness |
| `POST /v1/investigate` | Body = engine report JSON → investigation report |

Set `RECON_AGENT_MODE=anthropic` (and `ANTHROPIC_API_KEY`) to back the service
with Claude.

### Docker

```bash
docker build -t recon-dispute-agent .
docker run -p 8000:8000 recon-dispute-agent      # offline; runs as non-root
```

## How it works

For each discrepancy: an **investigation** tool-use loop gathers evidence and ends
by calling `submit_resolution`; a **verifier** loop independently approves or
rejects it; unproven findings are escalated. Full write-up in
[docs/agent-design.md](docs/agent-design.md) and
[docs/architecture.md](docs/architecture.md).

```
engine report ─▶ DisputeAgent ─▶ per discrepancy: investigate (tools) ─▶ verify ─▶ escalate?
```

## Root-cause taxonomy

A closed, routable set keyed to how payments break: `TIMING_LAG`,
`DUPLICATE_WEBHOOK`, `FEE_SCHEDULE_DRIFT`, `ROUNDING_OR_FX`, `UNRECORDED_REFUND`,
`FAILED_REFUND`, `MISSING_CAPTURE`, `UNKNOWN` — each mapped to a least-invasive
recommended action. See [`models.py`](src/recon_agent/models.py).

## Testing

```bash
make test    # pytest (31 tests, no network)
make lint    # ruff + mypy
```

The suite drives the **real agent control flow** through the offline brain (no
network): contract parsing, the tool registry and knowledge base, the full
investigate→verify→escalate path over the engine's committed example report, the
escalation-under-uncertainty guarantee, malformed-model-output resilience, robust
JSON extraction, and — via an injected fake — the real Claude adapter's response
parsing and request shaping. CI additionally runs `ruff` and `mypy` on Python
3.11 and 3.12.

## End-to-end with the engine

The agent reads a report from a file or from stdin (`-`), so it composes directly
with the engine in a single pipeline:

```bash
# in the payment-reconciliation-engine repo:
reconcile --psp settlement.csv --ledger ledger.csv --format json \
  | recon-agent -
```

The Go engine emits the discrepancy report; the Python agent investigates each
finding and prints (or emits, with `--format json`) the resolution — reconciliation
break to reasoned, verified recommendation in one command.

## Companion project

[**payment-reconciliation-engine**](https://github.com/jatin-gl/payment-reconciliation-engine)
— the Go service that produces the discrepancy reports this agent consumes.

## License

MIT — see [LICENSE](LICENSE).
