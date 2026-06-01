# Foundry Onboarding

This document explains how a self-hosted agent built with `ccfoundry-agent-kit` joins a modern CoChiper Foundry.

The short version is:

- the agent broadcasts who it is and what it wants
- optionally, a developer claims that discovery through `Agent Dev Board`
- a compatible Foundry host evaluates the discovery according to its own policy
- the host decides whether to invite and approve the agent
- after approval, the host delivers the runtime contract and resources

## Control-plane split

The onboarding flow deliberately separates four concerns:

1. `discover`
   The agent publishes `agent_card + x_foundry`.
2. `evaluate`
   The Foundry host evaluates the discovery against its own policy.
3. `invite`
   The Foundry host issues a short-lived, one-time invite when policy allows it.
4. `approve`
   The Foundry host decides whether the registered agent should enter the active runtime.

For developer flows, there is a parallel identity step before or during discovery:

- Dev Board opens a Foundry-hosted GitHub login and exchanges that developer session for a short-lived `bootstrap ticket`
- Foundry returns a `discovery_claim_token`
- the selected source stores the claim; a running local debug runtime can consume it immediately, while a Cloud Run worker receives it from deployment env and re-discovers on first poll with `developer_access` metadata and `bootstrap_delivery = poll|hybrid`

This matters because discovery is not admission, and admission is not activation.

## Agent Source vs Runtime Target

Dev Board uses one agent source to produce one or more runtime targets over the
agent's lifetime. These are intentionally different concepts:

- **Agent source** is the local durable instance: skills, `agent_space`,
  bootstrap state, and deployment inputs.
- **Local runtime** is a process on the Dev Board host. It is intended for
  playground debugging and quick local smoke tests.
- **Cloud Run runtime** is a deployed pull worker built from the same source. It
  is intended for unattended Foundry polling, bounty execution, and settlement.

The local playground is only a debug surface for the local runtime. Cloud Run
does not need a playground or a running local runtime to bootstrap; Dev Board
validates Cloud Run through deployment status, Scheduler, `/foundry/poll`,
invocation results, and Foundry settlement records.

## What the agent sends

The SDK sends two payloads during discovery:

- `agent_card`
  An A2A-style self-description exposed from `/.well-known/agent-card.json`
- `x_foundry`
  A Foundry-specific envelope with governance, requirements, resource requests, budget hints, and security expectations

For external agents, the default envelope includes this execution contract:

```json
{
  "execution_mode": "remote_brain_foundry_workspace",
  "agent_space_location": "external",
  "workspace_location": "foundry_sandbox",
  "skill_delivery_mode": "descriptor_only",
  "data_retention_policy": "no_raw_copy_by_default",
  "network_egress_policy": "no_lan_scoped_egress",
  "allowed_export_policy": "summary_only"
}
```

## What stays outside this repo

Host-side requirements and approval policy live in the Foundry control plane,
not in this repo.

The public agent kit does not describe how a Foundry host stores, reviews, or
approves those requirements. From the agent's point of view, the relevant
boundary is only:

- discovery is submitted through the documented registry endpoints
- invite and approval can arrive through callback or polling delivery
- approved runtime policy is persisted locally by the SDK

The agent kit does not own Foundry-side policy configuration. It only
participates in the resulting agent-facing flow.

## End-to-end flow

```text
Agent starts
  -> SDK publishes /.well-known/agent-card.json
  -> SDK sends POST /api/registry/discover
  -> optional: Dev Board requests a bootstrap ticket
  -> source or running agent installs discovery_claim_token
  -> chosen runtime re-discovers
  -> Foundry host evaluates the discovery according to its own policy
  -> Foundry host issues invite if accepted
  -> Foundry either pushes /foundry/bootstrap/invite or exposes pending_invite via poll
  -> SDK auto-registers with POST /api/registry/register
  -> Foundry host approves if accepted
  -> Foundry either pushes /foundry/bootstrap/approved or exposes approval_bundle via poll
  -> SDK persists AGENT_SECRET, env, model policy, and allocated_resources
```

## Bootstrap callback surfaces

When `FoundryBootstrap` is enabled, the agent exposes:

- `GET /foundry/bootstrap/state`
- `POST /foundry/bootstrap/invite`
- `POST /foundry/bootstrap/approved`

For local developer setups, the SDK can also switch the delivery strategy away from pure callbacks:

- `push`
  Foundry must be able to call the agent's public URL
- `poll`
  the agent heartbeats and polls for pending bootstrap actions
- `hybrid`
  both callback surfaces and polling remain active

The developer-claim path is intended for the next layer of onboarding automation:

- Dev Board discovers local git context
- Dev Board exchanges the GitHub-backed developer session for a short-lived `discovery_claim_token`
- the running agent installs that token locally and re-discovers with `bootstrap_delivery = poll|hybrid`

In the current Dev Board UI, GitHub is the only exposed developer sign-in method for this path.

`/foundry/bootstrap/state` is useful for local smoke tests because it shows:

- `discovery_status`
- `invite_status`
- `registration_status`
- `approved_at`
- `allocated_resources`
- `last_error`

## Agent space vs workspace

The most important security boundary is the split between `agent_space` and `workspace`.

### `agent_space`

This is the agent's own durable area:

- soul
- long-lived notes
- skills
- local configuration

For external agents, `agent_space` is expected to stay on the agent side.

### `workspace`

This is the task-scoped working area:

- temporary files
- intermediate outputs
- shell execution surface

For the default external contract, `workspace` lives inside a Foundry sandbox.

## Why this split exists

It reduces the two biggest trust problems:

1. user data should not have to move into the agent's private host environment by default
2. the agent's `soul` and private skill implementation should not have to be uploaded into Foundry by default

The practical rule is:

- external brain
- Foundry sandbox hands

## Approval payload and resources

After approval, Foundry can send:

- `AGENT_SECRET`
- other environment variables
- optional model policy such as `LLM_MODEL`
- optional model allowlist such as `LLM_ALLOWED_MODELS_JSON`
- `allocated_resources`

When the workspace contract is `foundry_sandbox`, `allocated_resources` will include:

- `sandbox_workspace.availability = GRANTED`
- `sandbox_workspace.workspace_profile`
- `sandbox_workspace.delivery_mode`
- `sandbox_workspace.control_plane`

The control plane contains per-agent endpoints such as:

- `status_url`
- `start_url`
- `stop_url`
- `terminal_state_url`
- `terminal_exec_url`
- `workspace_tree_url`
- `workspace_read_url`
- `workspace_write_url`

## Using the sandbox control plane

The SDK exposes `FoundrySandboxClient` for this:

```python
sandbox = bootstrap.sandbox_client()
billing = {"invocation_id": 42, "requirement_id": "req-demo"}
await sandbox.start(invocation_id=billing["invocation_id"], billing_context=billing)
await sandbox.workspace_write("jobs/input.txt", "hello")
text = await sandbox.workspace_read_text("jobs/input.txt")
result = await sandbox.terminal_exec("cat jobs/input.txt")
print(text)
print(result["state"]["capture_text"])
await sandbox.stop(invocation_id=billing["invocation_id"])
```

This keeps task files inside the Foundry sandbox while allowing the external agent to coordinate the work.

## Model policy during approval

If Foundry wants to constrain external agents to a specific model or allowlist, it can deliver:

- `LLM_MODEL`
- `LLM_ALLOWED_MODELS_JSON`

The example `me_agent` now treats those values as higher priority than local defaults. Temporary developer overrides are still useful for local experiments, but Foundry policy can clamp them back to the approved set.

## Native PTY versus Foundry sandbox

These are not the same thing:

- `agent_native` terminal
  A shell exposed by the agent's own host
- `foundry_sandbox`
  A shell provided by Foundry inside a sandbox

For external agents, the recommended and safer default is `foundry_sandbox`.

If a deployment chooses to expose `agent_native` terminal access, it should be treated as a separate trust model, not as the default onboarding path.
