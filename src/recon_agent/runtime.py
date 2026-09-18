"""Wiring: build a :class:`DisputeAgent` for a given mode.

* ``offline`` (default) — the deterministic :class:`HeuristicClient`. No API key,
  fully reproducible; used by the tests and for demos.
* ``anthropic`` — the real Claude-backed :class:`AnthropicClient`. Requires
  ``ANTHROPIC_API_KEY``.

In a real deployment the :class:`KnowledgeBase` would be replaced by adapters
over the production ledger, PSP API, and events store; here it defaults to the
seeded in-memory dataset so everything runs standalone.
"""

from __future__ import annotations

import os

from .agent import DisputeAgent
from .knowledge import KnowledgeBase
from .llm import AnthropicClient, HeuristicClient, LLMClient

Mode = str  # "offline" | "anthropic"


def make_llm(mode: Mode) -> LLMClient:
    if mode == "anthropic":
        return AnthropicClient()
    if mode == "offline":
        return HeuristicClient()
    raise ValueError(f"unknown mode {mode!r} (want 'offline' or 'anthropic')")


def build_agent(mode: Mode | None = None, knowledge: KnowledgeBase | None = None) -> DisputeAgent:
    mode = mode or os.getenv("RECON_AGENT_MODE", "offline")
    return DisputeAgent(llm=make_llm(mode), knowledge=knowledge or KnowledgeBase.default())
