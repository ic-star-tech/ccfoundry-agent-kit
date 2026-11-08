# SDK Guide

The Python SDK provides the agent-facing building blocks for both local development and modern Foundry onboarding.

This document is specifically about the Python runtime layer: manifests, chat contracts, `create_agent_app(...)`, bootstrap, and the sandbox client.

If you are looking for the browser-based local harness, local runtime management, or the Dev Board onboarding UX, see [Agent Dev Board](agent-dev-board.md).

## Main building blocks

1. `AgentManifest`
   Declares the agent's identity and capabilities.
2. `ChatRequest` / `ChatResponse`
   Defines the request/response contract for direct and inline chat.
3. `create_agent_app(...)`
   Wraps a chat handler into a FastAPI app.
4. `AgentSpace`
   Reads and writes local agent-owned files.
5. `FoundryBootstrap`
   Handles discovery, developer-claim install, invite delivery, register, and approval.
6. `FoundrySandboxClient`
   Operates a Foundry-provided sandbox workspace after approval.

## Minimal example

```python
from ccfoundry_agent_kit import AgentManifest, ChatRequest, ChatResponse, create_agent_app

manifest = AgentManifest(
    name="hello_agent",
    label="Hello Agent",
    version="0.1.0",
    description="A tiny example agent.",
)

async def handle_chat(request: ChatRequest, agent_space):
    return ChatResponse(reply=f"Hello, {request.username}. You said: {request.message}")

app = create_agent_app(
    manifest=manifest,
    chat_handler=handle_chat,
    agent_space_dir="agent_space",
)
```

The generated app exposes the portable core plus default SDK helpers:

- `GET /health`
- `GET /manifest`
- `POST /chat`
- `GET /.well-known/agent-card.json`
- `POST /api/chat`
- `GET /api/reflections`
- `GET /api/workspace/tree`
- `GET /api/workspace/read`
- `PUT /api/workspace/write`
- `POST /api/workspace/upload`
- `DELETE /api/workspace/delete`
- `POST /api/workspace/rename`
- `POST /api/workspace/copy`
- `POST /api/workspace/move`

`POST /chat` is the plain JSON compatibility endpoint.

`POST /api/chat` returns JSON when `stream` is false or omitted. The response
includes `reply`, `model` and `usage`; handlers should supply the actual model
and token usage when known (defaults are an empty model and usage object).
With `stream: true` it emits the SSE events `step`, `token`, `message`, `error`,
and `done`. Each request invokes the handler once in either mode.

### Agent authentication

Setting `AGENT_SECRET` enables bearer authentication on operational HTTP and
WebSocket routes, including chat, workspace, reflections and polling. The SDK
also reads secrets delivered or rotated through `FoundryBootstrap` live state.
Missing/incorrect bearer headers receive 401; duplicate Authorization headers
are rejected. Removing or clearing a configured secret does not reopen the app.

Health, manifest, discovery card and the sanitized bootstrap state remain public
read endpoints. Invite/approval callbacks keep their `X-Foundry-Bootstrap-Token`
checks, and developer-claim updates keep their local-caller restriction.

`platform_authenticated_poll=True` delegates **only** `POST /foundry/poll` to an
authenticating deployment platform. Use it only behind an enforced identity gate;
it does not verify OIDC tokens in Python. The Cloud Run deployment script enables
this for Scheduler OIDC and explicitly enables the invoker IAM check while
disallowing unauthenticated access. Other operational routes still require the
agent bearer. The default remains agent bearer authentication for polling too.

Local development without any configured secret stays unauthenticated. Set
`require_agent_secret=True` in `create_agent_app(...)` to deny operations until a
secret is available. Native PTY access always requires a valid bearer.

Use `AgentClient(base_url, agent_secret=secret)` to call a protected agent.
`stream_foundry_chat` explicitly requests streaming and only falls back to
`/chat` on 404/405. Authentication errors, server errors, malformed responses,
timeouts and broken streams propagate without executing the handler again.

When `FoundryBootstrap` is enabled, the SDK also mounts the bootstrap callback surfaces documented below. When native terminal access is explicitly enabled, it can additionally mount `GET /pty` via WebSocket.

## Agent space

`AgentSpace` is the local durable home for agent-owned files such as:

- `SOUL.md`
- `config.yaml`
- `notes.md`
- `todo.md`

This is intentionally conservative. The SDK helps with local state, but it does not force a database or orchestration model onto the agent.

## Foundry bootstrap

Enable `FoundryBootstrap` when the agent should automatically onboard into a modern Foundry:

```python
from ccfoundry_agent_kit import (
    AgentManifest,
    ChatRequest,
    ChatResponse,
    FoundryBootstrap,
    FoundryBootstrapConfig,
    FoundryDeveloperClaimPayload,
    create_agent_app,
)

manifest = AgentManifest(
    name="my_ext_agent",
    label="My External Agent",
    version="0.1.0",
    description="A self-hosted external agent.",
    capabilities=["chat", "notes"],
)

bootstrap = FoundryBootstrap(
    manifest=manifest,
    agent_space_dir=".",
    config=FoundryBootstrapConfig(
        enabled=True,
        foundry_base_url="http://127.0.0.1:9090",
        public_base_url="http://127.0.0.1:8085",
        network_zone="EXTERNAL",
        bootstrap_delivery="hybrid",
        tags=["notes", "assistant"],
    ),
)

app = create_agent_app(
    manifest=manifest,
    chat_handler=handle_chat,
    agent_space_dir=".",
    foundry_bootstrap=bootstrap,
)
```

When enabled, the SDK will:

1. publish an A2A-style `Agent Card`
2. send `POST /api/registry/discover`
3. keep the discovery alive with heartbeat
4. accept `POST /foundry/bootstrap/invite`
5. auto-register with `POST /api/registry/register`
6. accept `POST /foundry/bootstrap/approved`
7. persist the approved secret, environment, model policy, and resource bundle in `.foundry_bootstrap.json`

For developer setups where Foundry cannot reliably call back into the local machine, switch the delivery mode to `poll` or `hybrid`.

- `push`
  Foundry uses direct callbacks to `/foundry/bootstrap/invite` and `/foundry/bootstrap/approved`
- `poll`
  the agent keeps heartbeating and also polls Foundry for pending bootstrap actions
- `hybrid`
  keep the callback surfaces enabled, but also poll so local or NAT-scoped development still works

You can also attach a short-lived developer claim token at runtime:

```python
await bootstrap.install_developer_claim(
    FoundryDeveloperClaimPayload(
        discovery_claim_token="dev-claim-...",
        bootstrap_delivery="poll",
        foundry_base_url="https://foundry.example.com",
        public_base_url="http://127.0.0.1:8085",
        developer_identity={"github_login": "alice"},
        force_rediscover=True,
    )
)
```

The SDK also exposes `POST /foundry/bootstrap/developer-claim` so a local tool such as `Agent Dev Board` can install that payload into a running process.

## Foundry sandbox client

For external agents using the default secure execution contract, Foundry keeps the task workspace inside its own sandbox and sends the agent a control plane after approval.

Build a client from the live bootstrap object:

```python
sandbox = bootstrap.sandbox_client()
await sandbox.start(invocation_id=42, requirement_id="req-sync-fifo")
await sandbox.workspace_write("jobs/request.txt", "convert this PDF summary")
state = await sandbox.terminal_exec("ls -la && cat jobs/request.txt")
print(state["state"]["capture_text"])
stop_result = await sandbox.stop(invocation_id=42)
print(stop_result.get("runtime_cost"))
```

Available helper methods:

- `status()`
- `start(invocation_id=None, requirement_id="", billing_context=None)`
- `stop(invocation_id=None)`
- `terminal_state(capture_lines=80)`
- `terminal_exec(command, wait_ms=250, capture_lines=80, clear_line=False, enter=True)`
- `workspace_tree(depth=3)`
- `workspace_read(path)`
- `workspace_read_text(path)`
- `workspace_write(path, content="")`

The client automatically uses:

- `FoundryBootstrap.config.foundry_base_url`
- the approved `AGENT_SECRET`
- `allocated_resources.sandbox_workspace.control_plane`

For paid bounty work, pass the Foundry-provided `billing_context.invocation_id` to `start()` and `stop()`. Foundry uses that ID to attribute sandbox runtime cost to the task settlement.

## Foundry-provided model policy

If Foundry includes a model policy in approval env vars, agents can read:

- `LLM_MODEL`
- `LLM_ALLOWED_MODELS_JSON`

The bundled `me_agent` now honors those values ahead of its local config defaults.

For paid bounty work, include the same billing context in OpenAI-compatible requests that use Foundry-provided LLM resources:

```python
from ccfoundry_agent_kit import foundry_llm_metadata

billing = payload.get("billing_context", {})
metadata = foundry_llm_metadata(billing, agent_name="verilog-module-writer")
response = await client.chat.completions.create(
    model=model,
    messages=[{"role": "user", "content": prompt}],
    extra_body={"metadata": metadata},
)
```

Foundry's LiteLLM callback uses this metadata to write model usage to the same `invocation_id` ledger as sandbox runtime.

## Security boundary

The recommended external-agent contract is:

- `execution_mode = remote_brain_foundry_workspace`
- `agent_space_location = external`
- `workspace_location = foundry_sandbox`

In that model:

- the agent keeps its private `agent_space`
- Foundry owns the task workspace and shell execution surface
- the agent does not need native host terminal access

## Version, Errors And Schemas

`ccfoundry_agent_kit.__version__` is the single source of the SDK version (`pyproject.toml` reads it). The bootstrap sends it to Foundry as `x_foundry.system_manifest.sdk_version` and `profile_metadata.sdk_version`, so the server can tell which SDK an agent runs.

Calls from the SDK to Foundry (bootstrap, pull runtime, sandbox client) raise structured errors for non-2xx responses:

| Exception | When |
|---|---|
| `FoundryAuthError` | HTTP 401 / 403 |
| `FoundryNotFoundError` | HTTP 404 |
| `FoundryRateLimitError` | HTTP 429 (`retry_after` from `Retry-After`) |
| `FoundryServerError` | HTTP 5xx |
| `FoundryHTTPStatusError` | any other non-2xx, and the base of the classes above |

All of them derive from `FoundryAPIError` (with `method`, `path`, `status_code`, `response_excerpt`) and from `httpx.HTTPStatusError`, so existing `except httpx.HTTPStatusError` code keeps working. Network failures are still `httpx` transport errors.

`FoundryBootstrap.state.last_error` keeps its stable code as a prefix and appends what failed, for example `bootstrap_register_failed: HTTP 410 POST /api/registry/register: invite expired` or `bootstrap_discovery_failed: ConnectError`.

The JSON Schemas in `schemas/` are generated from the pydantic models; a test fails when they drift. Regenerate after changing `models.py`:

```bash
PYTHONPATH=packages/python-sdk/src python -m ccfoundry_agent_kit.schemas schemas/
```
