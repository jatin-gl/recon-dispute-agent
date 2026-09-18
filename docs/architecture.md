# Architecture

```
 engine report (JSON, contract v1.0)
          │
          ▼
   ┌──────────────┐     for each discrepancy
   │ DisputeAgent │──────────────────────────────┐
   └──────────────┘                               │
          │                                        ▼
          │                            ┌───────────────────────┐
          │            investigate ->  │  tool-use loop         │
          │                            │  (LLMClient + tools)   │
          │                            └───────────┬───────────┘
          │                                        │ submit_resolution
          │                                        ▼
          │                               Resolution (root cause,
          │                                confidence, action)
          │                                        │
          │              verify ->      ┌───────────────────────┐
          │                            │  verifier loop         │
          │                            │  (LLMClient + tools)   │
          │                            └───────────┬───────────┘
          │                                        │ submit_verification
          ▼                                        ▼
   InvestigationReport  <──  escalate if rejected / low-confidence / UNKNOWN
```

## Modules

| Module | Responsibility |
|---|---|
| `models` | Pydantic types for the input **data contract** (mirrors the engine's v1.0 JSON) and the agent's **output** (root-cause taxonomy, resolution, verification, investigation). |
| `knowledge` | `KnowledgeBase` — an in-memory stand-in for the ledger, PSP, and events systems of record. Swap for real adapters in production. |
| `tools` | Tool definitions + a `ToolRegistry`. Investigation tools read the knowledge base; terminal tools (`submit_resolution`, `submit_verification`) are captured by the loop. |
| `llm` | The `LLMClient` protocol and two implementations: `AnthropicClient` (Claude) and `HeuristicClient` (deterministic offline brain). |
| `prompts` | Investigator and verifier system prompts. |
| `agent` | `DisputeAgent` — the provider-agnostic tool-use loop, the verifier pass, and the escalation decision. |
| `runtime` | `build_agent(mode)` wiring. |
| `service` | FastAPI app (`/healthz`, `POST /v1/investigate`). |
| `cli` | `recon-agent` command. |

## The LLM boundary

Everything the agent does routes through one method:

```python
class LLMClient(Protocol):
    def complete(self, system, messages, tools) -> LLMResponse: ...
```

`AnthropicClient` implements it against Claude (`client.messages.create`, one
model turn per call, adaptive thinking on). `HeuristicClient` implements the same
interface with deterministic rules — it plays the same role a model would (fetch
evidence with the tools, then submit a structured verdict) but is fully
reproducible and needs no API key.

Because the boundary is this thin, **the agent's control flow — the loop, the
verifier, escalation — is identical for both**. The tests drive the real control
flow through the offline client, so what CI verifies is the same machinery that
runs against Claude.

## Data-contract coupling

The input types in `models.py` mirror the
[engine's data contract](https://github.com/jatin-gl/payment-reconciliation-engine/blob/main/docs/data-contract.md)
(v1.0). Money stays integer minor units end to end; the agent never applies
floating-point math to an amount. `tests/fixtures/example-report.json` is a
verbatim copy of the engine's committed example output, so the two projects are
tested against the same bytes.

**Forward compatibility.** The contract will evolve — a newer engine may emit a
discrepancy type this version of the agent doesn't know. Parsing is therefore
lenient: an unrecognized `type` is mapped to an `OTHER` sentinel (the original
string preserved in `raw_type` and surfaced in the output), and an unrecognized
`severity` is treated as `high`. Both push the item toward escalation rather than
failing the whole report — one unknown value never drops the findings the agent
*does* understand. See `Discrepancy._tolerate_unknown_contract_values`.
