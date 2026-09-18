# Contributing

Thanks for your interest. This is a portfolio/reference project, but it is built
to production standards and contributions that keep it there are welcome.

## Development setup

Requires Python 3.11+.

```bash
git clone https://github.com/jatin-gl/recon-dispute-agent
cd recon-dispute-agent
make install       # venv + editable install with dev extras
make test          # pytest
make run-example   # investigate the bundled report, offline
```

## Before opening a PR

Run the full local gate — CI runs the same checks:

```bash
make lint   # ruff + mypy
make test   # pytest
```

`make format` auto-fixes most lint findings.

## Standards

- **The LLM boundary stays thin.** All model interaction goes through the
  `LLMClient` protocol (`complete()`). New agent behavior must work identically
  under both `AnthropicClient` and the offline `HeuristicClient`, so it stays
  testable without an API key.
- **No floating-point money.** Amounts are integer minor units end to end.
- **Fail closed.** Ambiguous or malformed model output must degrade to
  `MANUAL_REVIEW` / escalation, never a confident guess or an unhandled
  exception. New terminal-tool handling needs a test proving the fallback.
- **Tests accompany behavior.** New tools, root causes, or endpoints need tests;
  keep the offline path deterministic.
- Keep the code `ruff`-clean and `mypy`-clean.

## Commit messages

Clear, imperative subject lines. Explain the *why* in the body when it isn't
obvious from the diff.
