# Agent Dev Board

`Agent Dev Board` is the local developer harness for this repository.

This document is about the local API/web harness, guided setup flow, local runtime management, and developer bootstrap UX.

It does not re-document the Python SDK surfaces or the agent runtime wire contract. For those, see [SDK Guide](sdk.md) and [Protocol](protocol.md).

Its implementation lives in:

- `apps/agent-dev-board-api`
- `apps/agent-dev-board-web`

## One-command startup

From the repository root:

```bash
npm run dev-board
```

This launcher is intentionally repo-local. It does not require a prior root-level `npm install`.
It will:

- create or reuse `.venv`
- install the Python SDK, example agent, and Dev Board API
- install the web dependencies for `apps/agent-dev-board-web` when needed
- start the API and web UI together
- create a runtime registry in `.dev-board/`
- let the board create local agents from template, each with its own name, port, workspace, logs, and bootstrap state

For LAN access:

```bash
npm run dev-board:lan
```

Its purpose is to make agent-side development and Foundry bootstrap debugging easy:

- create and manage local agents
- inspect manifests and runtime metadata
- send direct or inline chat requests
- apply temporary LLM overrides
- probe Foundry handshake state
- inspect local git and GitHub context
- request developer bootstrap tickets

## Templates, Models, And Skill Payloads

The board currently ships two local-agent templates:

- `me_agent`
- `verilog_module_writer`

Both templates default to `gemini-3.5-flash` in `agent_space/config.yaml`. The reflection micro/daily model settings use the same default so newly created agents have consistent chat and reflection behavior.

Each local agent is materialized into its own runtime directory under `.dev-board/agents/<agent>/`. The running agent reads skills from that instance directory, not directly from the repository template. Store skills that require companion files must therefore install those files into the instance skill directory. For example, `ip_reference` installs its `references/` portfolio so Auto Execute can find files such as:

```text
.dev-board/agents/<agent>/agent_space/skills/ip_reference/references/rra/rra.v
.dev-board/agents/<agent>/agent_space/skills/ip_reference/references/rra/rra_tb.v
```

If an existing agent was created before a resource-bearing skill copied its resources, reinstall that skill or copy the missing resource directory into the agent instance before running bounty execution.

## Components

- `apps/agent-dev-board-api`
  A small FastAPI service that creates local agents from templates, proxies requests to configured local agents, and talks to Foundry-side developer bootstrap endpoints.
- `apps/agent-dev-board-web`
  A React UI with three top-level views:
  - guided setup
  - agent card
  - agent playground

  Inside `agent card`, the current tabs are:
  - overview
  - local runtimes
  - runtime LLM
  - profile

## Request Flow

```text
Browser
  -> Agent Dev Board Web
  -> Agent Dev Board API
  -> local agent runtime created from template
  -> optional Foundry developer/bootstrap endpoints
```

For chat, the board consumes the agent's `POST /api/chat` SSE surface and renders step/token/message events.

For developer bootstrap, the board can:

- detect local git remotes
- inspect GitHub identity and repository context through the local API
- open a Foundry-hosted GitHub OAuth popup and receive a short-lived developer bootstrap token
- default to `https://foundry.cochiper.com`, while also exposing quick presets for `https://foundry.cochiper.ai` and custom Foundry URLs
- call Foundry `bootstrap-ticket` endpoints
- install the returned `discovery_claim_token`, Foundry URL, and public base URL into a running agent

In the current browser flow, GitHub is the only supported developer sign-in method.

## Non-goals

- multi-tenant RBAC
- production task orchestration
- production billing enforcement
- full Foundry admin surface

Those remain part of the Foundry control plane.

See also:

- [Architecture](architecture.md)
- [Foundry Onboarding](foundry-onboarding.md)
