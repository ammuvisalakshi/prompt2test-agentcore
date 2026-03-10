# ──────────────────────────────────────────────────────────────────────────────
# Prompt2TestAgentCore — Dockerfile
#
# Uses a minimal Python 3.12 slim image.
#
# No Playwright, no Chromium, no Node.js needed here because:
#   - Browser automation is handled by AgentCore Browser (managed cloud service)
#   - The agent connects to it over HTTPS via sse_client — no local browser process
#   - REST tools are handled by AgentCore Gateway (managed cloud service)
#
# Image size: ~200 MB (vs ~2 GB for the Playwright base image)
# ──────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim

WORKDIR /app

# curl is required for the HEALTHCHECK only
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# ── Python dependencies ────────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application source ─────────────────────────────────────────────────────────
COPY server.py agent_loop.py mcp_client.py saved_tests.py ./

# saved_tests.json is NOT baked into the image:
#   - Production: AgentCore Memory (set AGENTCORE_MEMORY_STORE_ID)
#   - Local dev:  written to ./saved_tests.json at runtime

# ── Runtime ────────────────────────────────────────────────────────────────────
EXPOSE 8000

ENV PYTHONUNBUFFERED=1

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["python", "server.py"]
