# Protocol

The minimum portable agent-side protocol in this repository is intentionally compact. The default SDK app also mounts a few convenience surfaces for local development and Foundry onboarding.

## Default runtime endpoints

### `GET /health`

Returns a health payload:

```json
{
  "status": "ok",
  "agent": "me_agent",
  "version": "0.1.0"
}
```

### `GET /manifest`

Returns the agent manifest:

```json
{
  "name": "me_agent",
  "label": "Me Agent",
  "version": "0.1.0",
  "description": "Self-hosted personal agent example.",
  "capabilities": ["chat", "notes", "inline"]
}
```

### `POST /chat`

Request:

```json
{
  "message": "Please remind me to review work every Friday.",
  "mode": "direct",
  "username": "demo-user",
  "context": "",
  "conversation_id": "demo-1"
}
```

Response:

```json
{
  "reply": "Got it. I will remember that.",
  "notes_update": "Review work every Friday",
  "metadata": {
    "mode": "direct"
  }
}
```

### `POST /api/chat`

This is the default Foundry-facing SSE surface around the same chat handler. It accepts a Foundry-style payload, normalizes it into `ChatRequest`, and streams events such as:

- `step`
- `token`
- `message`
- `error`
- `done`

`POST /chat` remains the simple JSON compatibility surface.

## Workspace and reflection surfaces

In addition to the core runtime endpoints, `create_agent_app(...)` mounts these SDK convenience surfaces:

- `GET /api/workspace/tree`
- `GET /api/workspace/read`
- `PUT /api/workspace/write`
- `POST /api/workspace/upload`
- `DELETE /api/workspace/delete`
- `POST /api/workspace/rename`
- `POST /api/workspace/copy`
- `POST /api/workspace/move`
- `GET /api/reflections`

These helpers are part of the default SDK runtime, but they are not the minimum interoperability bar for an external caller.

## Discovery surfaces

### `GET /.well-known/agent-card.json`

Returns an A2A-style Agent Card so Foundry can inspect the agent before invitation.

When bootstrap is enabled, the SDK uses this card during `POST /api/registry/discover`.

## Bootstrap callback surfaces

When `FoundryBootstrap` is enabled, the SDK also exposes:

### `GET /foundry/bootstrap/state`

Returns the current onboarding state, including discovery, invite, registration, approval, and resource information.

### `POST /foundry/bootstrap/invite`

Receives a one-time invite from Foundry. The SDK verifies the callback token and auto-registers with `POST /api/registry/register`.

### `POST /foundry/bootstrap/approved`

Receives the final approval payload from Foundry, including:

- `AGENT_SECRET`
- environment variables
- `allocated_resources`

### `POST /foundry/bootstrap/developer-claim`

Accepts a short-lived developer claim payload so a local Dev Board can install:

- `discovery_claim_token`
- `bootstrap_delivery`
- `developer_identity`

## Optional native PTY surface

### `GET /pty` via WebSocket

This exists only when the agent is explicitly configured with:

- `workspace_features` containing `terminal`
- `terminal_provider = agent_native`

This is an agent-host shell, not a Foundry sandbox shell.

For external agents, the recommended path is to use the Foundry sandbox control plane instead of exposing native PTY by default.

## Modes

- `direct`
  The user is talking directly to the agent.
- `inline`
  The agent is being called from another conversation and should usually respond more concisely.
