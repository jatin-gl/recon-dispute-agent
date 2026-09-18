FROM python:3.12-slim AS base

# Don't buffer stdout/stderr; don't write .pyc files.
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install dependencies first for better layer caching.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# Runs in deterministic offline mode by default (no API key required).
# Set RECON_AGENT_MODE=anthropic and ANTHROPIC_API_KEY to use Claude.
ENV RECON_AGENT_MODE=offline
EXPOSE 8000

# Run as a non-root user.
RUN useradd --create-home appuser
USER appuser

CMD ["uvicorn", "recon_agent.service:app", "--host", "0.0.0.0", "--port", "8000"]
