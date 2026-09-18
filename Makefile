# recon-dispute-agent — developer tasks.
# Uses a local .venv. Override PY to point at a different interpreter.
PY ?= python3
VENV := .venv
BIN := $(VENV)/bin

.PHONY: help venv install test lint format run-example run-server docker clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

venv: ## Create the virtualenv
	$(PY) -m venv $(VENV)

install: venv ## Install the package with dev extras
	$(BIN)/pip install -q --upgrade pip
	$(BIN)/pip install -q -e ".[dev]"

test: ## Run the test suite
	$(BIN)/pytest

lint: ## Lint (ruff) and type-check (mypy)
	$(BIN)/ruff check src tests
	$(BIN)/mypy src/recon_agent

format: ## Auto-fix lint findings
	$(BIN)/ruff check --fix src tests

run-example: ## Investigate the bundled sample report (offline mode)
	$(BIN)/recon-agent tests/fixtures/example-report.json

run-server: ## Run the FastAPI service on :8000
	$(BIN)/uvicorn recon_agent.service:app --reload --port 8000

docker: ## Build the service container image
	docker build -t recon-dispute-agent:latest .

clean: ## Remove caches and build artifacts
	rm -rf $(VENV) .pytest_cache src/*.egg-info build dist
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
