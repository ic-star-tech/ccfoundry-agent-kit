# Architecture

This repository contains six main layers:

1. the agent runtime SDK
2. an example self-hosted agent
3. a lightweight local coordinator API
4. a lightweight local developer web UI
5. a Cloud Run deployment pipeline
6. an AP2-inspired payment settlement layer

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
- `FoundryBootstrap`
- `FoundrySandboxClient`
- `FoundryPullRuntime`
- `BillingContext` / `SettlementBreakdown`
- `FoundryMandate` / `MandateItem` / `SettlementRecord` / `SettlementNotification`
- `create_intent_mandate()` / `create_cart_mandate()` / `create_settlement_mandate()`
- `sign_mandate()` / `verify_mandate()`
- `foundry_llm_metadata()`
- `TaskTracker`

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

When `FoundryBootstrap` is enabled, it also mounts:

- `GET /foundry/bootstrap/state`
- `POST /foundry/bootstrap/invite`
- `POST /foundry/bootstrap/approved`
- `POST /foundry/bootstrap/developer-claim`

When `AGENT_DEPLOY_MODE=cloud_run`, a Cloud Run polling endpoint is exposed:

- `POST /foundry/poll`

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
- install and remove local skills
- inspect Foundry jobs and settlement records
- launch a narrow Google Cloud Run deployment helper for selected local agents

### Why It Exists

Without this layer, every agent would need its own bespoke testing flow.

With it, developers get a thin, consistent harness for:

- agent discovery
- direct/inline mode simulation
- browser-based smoke testing
- developer bootstrap ticket requests
- handshake and bootstrap-state probes
- Skill Store, Job Board, Earnings, and Cloud Run smoke-test flows that exercise agent-facing contracts

### Why It Stays Small

It is intentionally not trying to become:

- multi-tenant persistence
- RBAC
- a production scheduler
- a registry service
- a payment processor
- a cloud operations platform

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
- bounty-success email notification sync
- Skill Store installation workflows
- Job Board browsing and claim helpers
- Earnings and settlement inspection (gross / resource cost / net breakdown)
- Google Cloud Run deploy, smoke-test, and live worker inventory panels

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
  -> Foundry host evaluates the discovery according to its own policy
  -> Foundry host issues a one-time invite if accepted
  -> Foundry pushes /foundry/bootstrap/invite
  -> agent auto-registers with POST /api/registry/register
  -> Foundry host approves if accepted
  -> Foundry pushes /foundry/bootstrap/approved with AGENT_SECRET + resources
```

This repo only owns the agent side of that loop. Requirement definition,
routing, invite issuance, and approval policy all live in the Foundry host and
are intentionally not described here as implementation details.

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

## Layer 5: Cloud Run Deployment Pipeline

`Dockerfile.cloudrun`, `scripts/deploy-cloudrun.sh`, and the Dev Board Cloud Run
panel form a reference deployment path for serverless agent operation.

### Architecture

```text
Cloud Scheduler (cron, default every minute)
    ↓ POST + OIDC Token
Cloud Run (agent container, scale-to-zero)
    ↓ POST /foundry/poll
FoundryBootstrap.heartbeat_once() + FoundryPullRuntime.poll_once()
    ↓ claim + process tasks
Foundry API (claim → sandbox → deliverable → settlement)
```

### Key Behaviors

- `AGENT_DEPLOY_MODE=cloud_run` disables internal polling loops.
- `POST /foundry/poll` performs heartbeat + up to 30 task claims in a single
  HTTP request cycle.
- The container scales to zero when idle.
- Dev Board can deploy, smoke-test, and retire Cloud Run workers.
- Bootstrap state is seeded from environment variables on ephemeral container
  filesystems.

See [Cloud Run Deployment](cloud-run-deployment.md) for full details.

## Layer 6: AP2-Inspired Payment Settlement

The SDK implements a three-layer mandate chain modeled after
[Google AP2](https://github.com/google-agentic-commerce/AP2) for verifiable
agent payment settlement.

### Mandate Chain

```text
IntentMandate  ≈  Foundry task brief with budget ceiling
    ↓
CartMandate    ≈  Agent's quote / bid with fee breakdown
    ↓
SettlementMandate ≈  Signed proof of verified payment
```

### Signing

- HMAC-SHA256 using the pre-established `AGENT_SECRET` as the shared key.
- Deterministic canonical JSON serialization for signing.
- Constant-time signature comparison to prevent timing side-channels.

### Resource Cost Accounting

The billing context flow ensures per-task resource cost attribution:

```text
Foundry assigns invocation_id
    ↓ bounty claim payload includes billing_context
Agent SDK passes invocation_id to:
    ↓ sandbox.start()
    ↓ LLM requests via foundry_llm_metadata()
    ↓ deliverable submit
Foundry aggregates resource costs by invocation_id:
    ↓ model_cost + sandbox_cost + feature_cost = resource_cost
    ↓ net_payout = gross_reward - resource_cost
    ↓ Stripe pays net_payout
    ↓ Settlement mandate is signed and delivered to agent
```

See [Resource Cost Accounting Plan](resource-cost-accounting-plan.md) for
full details.
