# Image that serves examples/me_agent as a Foundry AgentService
# (Marketplace local package). See docs/agentservice-packaging.md.
#
# Build from the repository root, for the appliance's CPU architecture:
#
#   docker buildx build --platform linux/arm64 \
#     -f examples/agentservice/Dockerfile \
#     -t local/me-agent:0.1.0 \
#     --output type=docker,dest=me-agent-0.1.0-arm64.tar .
#
# examples/agentservice/Dockerfile.dockerignore keeps local runtime state
# (bootstrap secrets, notes, reflections) out of the build context.

# --- Stage 1: install the SDK and the agent -----------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build

COPY packages/python-sdk/pyproject.toml packages/python-sdk/README.md packages/python-sdk/
COPY packages/python-sdk/src packages/python-sdk/src
COPY examples/me_agent/pyproject.toml examples/me_agent/README.md examples/me_agent/
COPY examples/me_agent/src examples/me_agent/src

# Install both together so pip takes the agent's ccfoundry-agent-kit
# dependency from this checkout rather than from PyPI.
RUN pip install --no-cache-dir --prefix=/install \
      ./packages/python-sdk \
      ./examples/me_agent \
      "uvicorn>=0.30,<1.0"

# --- Stage 2: runtime ---------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ME_AGENT_BASE_DIR=/app \
    FOUNDRY_DISCOVERY_ENABLE=false

RUN groupadd --system --gid 10001 agent \
 && useradd --system --uid 10001 --gid agent --home-dir /app --no-create-home \
      --shell /usr/sbin/nologin agent

COPY --from=builder /install /usr/local

WORKDIR /app
# me_agent writes notes, reflections and task state under agent_space/.
COPY --chown=agent:agent examples/me_agent/agent_space /app/agent_space
COPY examples/agentservice/foundry_service.py /app/foundry_service.py
RUN mkdir -p /app/workspace && chown agent:agent /app/workspace

USER agent

EXPOSE 8080

CMD ["python", "-m", "uvicorn", "foundry_service:app", \
     "--app-dir", "/app", \
     "--host", "0.0.0.0", \
     "--port", "8080"]
