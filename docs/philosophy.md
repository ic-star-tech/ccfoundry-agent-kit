# Philosophy

`ccfoundry-agent-kit` is built around a specific architectural conviction:

**Foundry provides the demands and infrastructure. Agents provide the skills and knowledge. Together they solve problems that neither can solve alone.**

## The Foundry-Agent Architecture

Most agent platforms take one of two extremes:

1. **Platform-centric** — The platform owns everything: the agent logic, the data, the tools, the execution. Agents are just plugins inside a monolithic system.
2. **Agent-centric** — Each agent is an isolated island. It has its own models, tools, and data, but no way to participate in a larger ecosystem.

The Foundry-Agent architecture is a deliberate third path. It defines a **collaboration contract** between two independent parties:

```
┌────────────────────────────────┐  ┌────────────────────────────────┐
│  Foundry                       │  │  Agent                         │
│  ───────                       │  │  ─────                         │
│  Receives user demands         │  │  Brings skills & knowledge     │
│  Provides AI infrastructure    │  │  Provides specialized logic    │
│  Manages sandboxed workspaces  │  │  Owns its private state        │
│  Routes tasks to agents        │  │  Can execute via Foundry       │
│  Can handle auth & audit       │  │  Declares its capabilities     │
│  Discovers and onboards agents │  │  Decides what to accept        │
└────────────────────────────────┘  └────────────────────────────────┘
                    ↕ structured protocol ↕
```

The key insight is that **neither side owns the other**. Foundry does not dictate how the agent thinks. The agent does not need to build its own infrastructure. Each side does what it is good at.

## Why This Split Matters

### For agent developers

You focus on what makes your agent valuable — its domain knowledge, its reasoning strategy, its skills — without worrying about infrastructure. Foundry can give you:

- LLM gateway access when the agent opts into a Foundry-managed model path
- Sandboxed workspaces (safe task execution without exposing your own filesystem)
- User routing (Foundry brings the demand — users who need your agent's skills)
- Onboarding and discovery (your agent announces what it can do; Foundry matches it to demand)

### For platform operators

You focus on infrastructure, security, and user experience without needing to understand or own every agent's internal logic. Agents arrive with their own skills and knowledge, declare their capabilities through a manifest, and operate through a well-defined contract.

### For users

You get access to multiple specialized agents through a single platform. Each agent brings genuine domain expertise rather than being a thin wrapper around the same generic model.

## Foundry Provides Demands and Infrastructure

Foundry's role is to connect user needs with agent capabilities:

1. **Demands** — Users come to Foundry with problems to solve. Foundry routes those problems to agents with matching skills.
2. **LLM Gateway** — Centralized model access with policy controls, so agents do not each need their own API keys and billing.
3. **Sandbox Workspaces** — Isolated execution environments for task work. The agent's private state stays separate from the task workspace.
4. **Discovery and Onboarding** — A structured process for agents to join the ecosystem: discover → review → invite → register → approve.
5. **Multi-agent Routing** — Users can switch between specialist agents or invoke them inline, each handling its own domain.
6. **Security and Audit** — Platform-side authentication, authorization, and audit controls that agents do not need to build themselves.

## Agents Provide Skills and Knowledge

The agent's role is to bring genuine value:

1. **Skills** — Defined in `agent_space/skills/`, these are composable capabilities the agent has learned or been taught. They are the agent's competitive advantage.
2. **Knowledge** — Accumulated in `agent_space/notes.md` and other local files. This is domain-specific understanding that makes the agent useful beyond what a generic model can offer.
3. **Soul** — Defined in `agent_space/SOUL.md`, this is the agent's identity, reasoning strategy, and behavioral contract. It shapes how the agent approaches problems.
4. **Specialized Logic** — The agent runtime can contain domain-specific processing that goes beyond prompt-in, response-out. The bundled `me_agent` is one concrete example of that pattern.

The agent brings these assets to Foundry and uses Foundry's infrastructure to put them to work — serving real users, executing tasks in sandboxes, accessing shared models.

## Sovereignty as the Trust Foundation

For this collaboration to work, both sides need trust boundaries.

The critical design rule: **the agent's `agent_space` never leaves its control by default.**

```
  agent_space (PRIVATE)             workspace (SHARED)
  ─────────────────────             ──────────────────
  Soul, knowledge, skills,          Task files, outputs,
  private state.                    shell execution area.

  Stays with the agent.             Lives in Foundry
  Never uploaded by default.        sandbox when connected.
  Agent controls what to expose.    Scoped to the task.
```

This separation means:

- The agent can join Foundry without revealing its internal reasoning or private data
- Under the default external-agent contract, Foundry can provide sandboxed execution without the agent needing native host access
- Users' task data stays in Foundry-managed spaces, not inside the agent's private environment

The trust boundary starts with the **manifest system** — the agent explicitly declares what it accepts:

| Declaration | What It Controls |
|------------|------------------|
| `llm.needs_gateway` | Whether to use Foundry's model access or bring its own |
| `dashboard.soul_visible` | Whether its SOUL.md is visible in Foundry dashboards |
| `dashboard.soul_editable` | Whether admins can edit its soul |
| `infra.heartbeat_managed` | Whether it accepts Foundry health monitoring |
| `mcp.accepts_foundry_mcp` | Whether it is willing to accept Foundry-provided MCP configuration metadata |

A fully open official agent and a privacy-conscious third-party agent can both use the same SDK and the same onboarding flow. The difference is only in what they declare.

For Foundry-connected agents, the execution contract is then completed through the Foundry bootstrap envelope, which carries details such as sandbox workspace requests, delivery mode, and the default `remote_brain_foundry_workspace` posture.

## Open Protocols, Not Lock-In

This project deliberately aligns with emerging open standards rather than inventing proprietary protocols:

- **A2A** (Google Agent-to-Agent Protocol) for agent discovery — so your agent can be found and engaged by any A2A-compatible system
- **MCP** (Model Context Protocol) as the vocabulary for manifest/config-level tool declarations, without a built-in MCP runtime yet
- **OpenAI Chat Completions** for model calls — so any compatible provider works without code changes
- **SSE** (Server-Sent Events) for streaming — because it is simple, well-supported, and sufficient

The reasoning is pragmatic, not ideological. Proprietary protocols create artificial barriers. Open standards make your agent more useful in a wider ecosystem. When the agent speaks A2A, any platform that understands A2A can discover it. The repo already ships A2A discovery and SSE streaming today, and it keeps MCP-related declarations aligned with the standard rather than inventing a proprietary schema.

See [Standards](standards.md) for the full rationale.

## Self-Hostable By Default

The example `me_agent` is intentionally not magical.

It uses local files, a small FastAPI surface, and optional OpenAI-compatible model calls. If no model credentials are configured, it still responds safely in a deterministic fallback mode.

That is not because fallback mode is the end goal. It is because developer experience matters:

- Local runs should not depend on hidden infrastructure
- The first successful request should happen quickly
- Users should be able to understand the runtime from the repository itself

An agent that can only function when connected to Foundry would undermine the architecture. The whole point is that agents are independently valuable — Foundry amplifies them, not defines them.

## Protocol Small, Behavior Rich

This repository deliberately keeps the minimum portable contract small:

- `GET /health`
- `GET /manifest`
- `POST /chat`
- `GET /.well-known/agent-card.json`

The default SDK app also mounts extra helper surfaces such as `POST /api/chat`, `GET /api/reflections`, and `/api/workspace/*` for local development and sandbox-style workflows. When Foundry bootstrap is enabled, it also mounts onboarding callbacks, and native host PTY remains an explicit opt-in rather than the default contract.

That split is a feature, not a limitation.

It creates room for agents to be implemented differently while still presenting a stable surface to callers. The SDK helps developers stand up runtimes quickly, but it does not force one worldview for persistence, orchestration, or product UX.

## Clear Open-Source Boundary

One of the most important choices in this repository is what it does not publish.

This project does not try to smuggle in:

- The full control plane
- Internal deployment topology
- Production data models
- Tenant management
- Internal registry infrastructure
- Private operational assumptions

That is deliberate.

Open-source is healthier when the boundary is honest. Publishing a clean agent-side toolkit is better than publishing a half-redacted platform snapshot that is hard for outsiders to run and hard for maintainers to support.

## The Practical Standard

The project should satisfy a practical bar:

1. A new developer can understand what the repo is for
2. They can run the example locally
3. They can build an agent with useful skills and knowledge
4. They can connect to Foundry when ready — and use Foundry's infrastructure to serve real users
5. Their agent's private state remains sovereign throughout
6. They can disconnect from Foundry without losing anything valuable

If those six things stay true, the repository is doing its job.
