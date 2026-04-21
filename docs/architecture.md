# Architecture

This repository contains four main layers:

1. the agent runtime SDK
2. an example self-hosted agent
3. a lightweight local coordinator API
4. a lightweight local developer web UI

Together they create a development loop that is useful without depending on the full Foundry control plane.

## Component Map

```text
.
├── packages/python-sdk/     # agent runtime primitives
├── examples/me_agent/       # self-hosted example agent
├── apps/agent-dev-board-api/   # Agent Dev Board API
└── apps/agent-dev-board-web/   # Agent Dev Board web UI
```

## Layer 1: Python SDK

The SDK provides the minimum portable contract plus a few default helper surfaces:

- `AgentManifest`
- `ChatRequest`
- `ChatResponse`
- `ContextMode`
- `create_agent_app(...)`
- `AgentSpace`

### Responsibility

The SDK is responsible for helping developers expose a consistent FastAPI runtime.

It is not responsible for:

- choosing your model vendor
- defining your database schema
- inventing your orchestration system
- owning your product UI

### HTTP Contract

The generated app always exposes the portable core:

- `GET /health`
- `GET /manifest`
- `POST /chat`
- `GET /.well-known/agent-card.json`

It also mounts default SDK helper surfaces:

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

That portable core is what keeps the runtime interoperable, while the helper surfaces keep local development practical.

## Layer 2: Example Agent

`examples/me_agent` shows how a self-hosted personal agent can be built on top of the SDK.

### Runtime Pattern

The example agent reads from local `agent_space/` files such as:

- `SOUL.md`
- `notes.md`
- `config.yaml`

It then combines those files with the incoming `ChatRequest` to decide how to respond.

### Behavior

The example illustrates several important patterns:

- direct vs inline chat mode
- optional LLM calls through an OpenAI-compatible provider
- deterministic fallback replies when no API key is configured
- durable note updates through `notes_update`

This keeps the example realistic without making it dependent on hidden infrastructure.

## Layer 3: Agent Dev Board API

`apps/agent-dev-board-api` is the API behind `Agent Dev Board`, not a production platform.

Its job is to:

- read a local runtime registry YAML file
- discover configured local agents
- fetch manifests
- proxy chat requests and stream SSE
- maintain simple in-memory transcript state for local conversations
- talk to Foundry developer-bootstrap endpoints

### Why It Exists

Without this layer, every agent would need its own bespoke testing flow.

With it, developers get a thin, consistent harness for:

- agent discovery
- direct/inline mode simulation
- browser-based smoke testing
- developer bootstrap ticket requests
- handshake and bootstrap-state probes

### Why It Stays Small

It is intentionally not trying to become:

- multi-tenant persistence
- RBAC
- a production scheduler
- a registry service

Those concerns would distort the purpose of the repository.

## Layer 4: Agent Dev Board Web

`apps/agent-dev-board-web` is the React/Vite `Agent Dev Board` that talks to the local coordinator API.

### Current Scope

It supports:

- the `Guided setup`, `Agent card`, and `Agent playground` views
- listing discovered agents
- agent playground chat
- switching direct vs inline mode
- sending a message
- viewing transcript history
- inspecting manifest metadata
- temporary `model / base_url / api_key` overrides
- Foundry handshake probes
- local git and GitHub context display
- developer bootstrap ticket requests

### Why The UI Is Minimal

This UI is meant to be a protocol exerciser, not a full workspace product.

That is why the architecture intentionally keeps the browser side thin and keeps the interesting behavior in the agent runtime and harness API.

## Request Flow

The normal local development path looks like this:

```text
User types in browser
  -> Agent Dev Board sends POST /api/chat
  -> Agent Dev Board API loads agent config from the runtime registry YAML
  -> Agent Dev Board API calls the agent's POST /api/chat endpoint
  -> agent runtime streams SSE events
  -> Agent Dev Board API stores transcript in memory
  -> browser updates transcript and latest reply
```

For manifest discovery:

```text
Browser loads
  -> Agent Dev Board calls GET /api/agents
  -> Agent Dev Board API reads the runtime registry YAML
  -> Agent Dev Board API calls each agent's GET /manifest
  -> browser renders agent list and manifest summary
```

## State Model

State is intentionally simple and split across layers:

- agent-owned durable local state lives in `agent_space/`
- Dev Board-owned local conversation state lives in memory
- browser state lives in React component state

This avoids pretending that a local harness is a durable multi-user platform.

## Integration Boundary With Full Foundry

This repository is designed so the same agent runtime can later be called by a fuller host platform.

The important boundary is:

- this repo defines the agent-side contract and local development path
- the full control plane, if present, sits outside this repo

That allows the public open-source package to remain honest and runnable.

## Modern Foundry Onboarding Path

When `FoundryBootstrap` is enabled, the agent participates in a longer control-plane flow:

```text
Agent starts
  -> publishes /.well-known/agent-card.json
  -> sends POST /api/registry/discover with agent_card + x_foundry
  -> Foundry matches the discovery against admin-defined requirements
  -> master-agent review may evaluate fit, resource alignment, and payment criteria
  -> admin issues a one-time invite
  -> Foundry pushes /foundry/bootstrap/invite
  -> agent auto-registers with POST /api/registry/register
  -> admin approves
  -> Foundry pushes /foundry/bootstrap/approved with AGENT_SECRET + resources
```

This repo only owns the agent side of that loop. Requirement definition, routing, invite issuance, and approval policy all live in the Foundry control plane.

There is also a developer-bootstrap variant of this path:

```text
Developer opens Agent Dev Board
  -> board inspects local git + GitHub context
  -> board opens the Foundry-hosted GitHub login popup
  -> board requests bootstrap ticket from Foundry
  -> board installs discovery_claim_token into running agent
  -> agent re-discovers with developer_access metadata
```

## Agent Space Versus Workspace

The current external-agent security model deliberately separates two assets:

- `agent_space`
  The agent's long-lived soul, notes, skills, and local private state.
- `workspace`
  The task-scoped files and shell execution surface used for actual work.

For the default external execution contract:

- `execution_mode = remote_brain_foundry_workspace`
- `agent_space_location = external`
- `workspace_location = foundry_sandbox`
- `skill_delivery_mode = descriptor_only`

That means:

- the agent keeps its long-lived identity and private files on its own side
- Foundry owns the task workspace and shell execution surface
- the agent receives a control plane to operate the Foundry workspace instead of receiving native host access

## Foundry Sandbox Hands

After approval, Foundry may deliver:

- `allocated_resources.sandbox_workspace`
- `allocated_resources.sandbox_workspace.control_plane`

The SDK exposes `FoundrySandboxClient` so the external brain can operate that workspace by API:

- start a sandbox lease
- inspect status
- write or read workspace files
- execute terminal commands inside the sandbox
- stop the lease

This is the intended path for external agents that need task execution while keeping `agent_space` private.

## Extension Points

Several extensions fit naturally on top of the current architecture:

- richer agent examples
- more SDK helpers
- alternative harness UIs
- adapter apps that bridge third-party runtimes into the same Foundry-facing contract
- OpenAI-compatible facade endpoints
- third-party UI integrations such as `Open WebUI`

The key rule is that new layers should sit on top of the current contract instead of muddying it.
