# Standards

This document explains which open standards `ccfoundry-agent-kit` aligns with, why, and how.

For the concrete HTTP routes, SSE events, and bootstrap callback surfaces exposed by the SDK runtime, see [Protocol](protocol.md).

## Why Open Standards

Many agent frameworks invent proprietary protocols for discovery, tool integration, and model communication. That creates lock-in — your agent only works inside one ecosystem, and integrating with anything else requires custom adapters.

We take the opposite approach. Wherever a credible open standard exists, we adopt it. That makes your agent portable across ecosystems, not just across deployment environments.

## A2A — Agent-to-Agent Protocol

### What It Is

[A2A](https://github.com/google/A2A) is an open protocol introduced by Google for secure, standardized communication between autonomous AI agents. It defines how agents discover one another, share capabilities, delegate tasks, and coordinate complex workflows — regardless of their underlying frameworks or vendors.

Think of A2A as HTTP for agents: a shared communication layer that makes interoperability possible.

### How We Use It

The SDK exposes an **Agent Card** at the well-known discovery endpoint:

```
GET /.well-known/agent-card.json
```

This follows the A2A specification (aligned with [RFC 8615](https://tools.ietf.org/html/rfc8615)). The Agent Card is a JSON document that describes:

- Agent identity (name, version, description)
- Capabilities and supported interaction modes
- Transport hints such as `url`, `supportedInterfaces`, and default input/output modes
- Provider metadata and capability flags
- Skill summaries derived from the manifest

When connecting to Foundry, the SDK sends the Agent Card alongside a Foundry-specific metadata envelope (`x_foundry`) during discovery:

```
POST /api/registry/discover
Body: { agent_card: {...}, x_foundry: {...} }
```

By separating the standard A2A card from the Foundry-specific metadata, other systems can inspect the discovery document without needing to understand the Foundry-specific `x_foundry` envelope.

### Key Design Choices

- The Agent Card is always served at the standard `/.well-known/` path, even when Foundry bootstrap is disabled
- Foundry-specific metadata (`x_foundry`) is an extension envelope, not a replacement for the standard card
- The card is self-describing — it contains enough information for another agent to decide whether collaboration is possible

---

## MCP — Model Context Protocol

### What It Is

[MCP](https://modelcontextprotocol.io) (Model Context Protocol) is an open standard from Anthropic that defines how AI agents connect to tools, data sources, and external services. If A2A is how agents talk to each other, MCP is how agents talk to their toolbox.

### Current Boundary In This Repo

The repository is MCP-aware at the manifest and configuration layer, but it does not yet ship a built-in MCP runtime.

Today the SDK and example agent expose:

- `manifest.mcp.accepts_foundry_mcp`
- `manifest.mcp.has_own_mcp`
- `agent_space/config.yaml` pointing at an `mcp_file` such as `agent_space/mcp_servers.yaml`

Those values are published so Foundry can inspect the agent's MCP posture during discovery and onboarding.

The SDK does not yet provide:

- a built-in MCP client
- automatic loading and execution of `mcp_servers.yaml`
- a Streamable HTTP or stdio MCP transport implementation

### Transport Direction

When runtime MCP support is added, the intended network direction is to align with the current MCP transport model rather than inventing a Foundry-specific tool protocol. That means Streamable HTTP is the likely fit for network transport, but this repo does not expose that transport today.

### MCP Configuration

The repo uses `agent_space/mcp_servers.yaml` as a conventional location for MCP server entries:

```yaml
# Managed by the agent or Foundry registry sync.
servers: {}
```

Right now that file is agent-owned configuration. The SDK does not automatically read it into a live MCP client session.




## OpenAI Chat Completions — Model Interface

### Why This Interface

The OpenAI Chat Completions API (`POST /v1/chat/completions`) has become the de facto standard for LLM interaction. Nearly every model provider offers a compatible endpoint: OpenAI, Anthropic (via adapter), Google (via adapter), Mistral, local Ollama, LiteLLM proxy, and many others.

### How We Use It

The example `me_agent` uses the `openai` Python client to make model calls. The provider is configured entirely through environment variables:

```bash
LLM_API_BASE=http://your-provider:4000
LLM_API_KEY=sk-...
LLM_MODEL=gpt-5.4-nano
```

This means:

- Switching model providers requires zero code changes
- Foundry can deliver model policy (preferred model, allowed models) after approval
- Local development can use Ollama or any OpenAI-compatible local server
- The agent is never hard-coded to one vendor

---

## SSE — Server-Sent Events

### Why SSE

Server-Sent Events are a simple, well-supported standard for server-to-client streaming over HTTP. They are natively supported by all modern browsers and trivially consumed by HTTP clients.

### How We Use It

The SDK uses SSE in two places:

**Chat streaming** (`POST /api/chat`). The agent streams execution progress and response chunks in real time:

```
event: step
data: {"status": "running", "text": "Searching memory..."}

event: step
data: {"status": "done", "text": "Memory search complete"}

event: token
data: {"content": "Based on your notes, "}

event: token
data: {"content": "here is what I found..."}

event: message
data: {"content": "Based on your notes, here is what I found..."}

event: done
data: {}
```

This gives callers real-time visibility into the agent's thinking process — not just the final answer.

**Bootstrap lifecycle.** During Foundry onboarding, the SDK uses callback and polling flows for discovery, invite, and approval, while local inspection happens through JSON state such as `GET /foundry/bootstrap/state`.

---

## How These Fit Together

```
┌─────────────────────────────────────────────────────────┐
│                    Your Agent                            │
│                                                         │
│   ┌─────────────────┐    ┌──────────────────────────┐  │
│   │  Agent Card      │    │  Chat Handler             │  │
│   │  (A2A Protocol)  │    │  (SSE Streaming)          │  │
│   └────────┬────────┘    └────────────┬─────────────┘  │
│            │                          │                  │
│   ┌────────▼────────┐    ┌────────────▼─────────────┐  │
│   │  /.well-known/   │    │  LLM Provider             │  │
│   │  agent-card.json │    │  (OpenAI Chat Completions)│  │
│   └─────────────────┘    └──────────────────────────┘  │
│                                                         │
│   ┌─────────────────────────────────────────────────┐  │
│   │  MCP Declarations / Config Hooks                │  │
│   │  (manifest fields + mcp_servers.yaml)           │  │
│   └─────────────────────────────────────────────────┘  │
│                                                         │
│   ┌─────────────────────────────────────────────────┐  │
│   │  Foundry Bootstrap                               │  │
│   │  (A2A Card + x_foundry → discover → register)    │  │
│   └─────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

The key insight is that these protocols handle different layers:

- **A2A** handles agent identity and discovery
- **MCP** is represented today as manifest/config declarations for future tool integration
- **OpenAI Completions** handles model access
- **SSE** handles real-time streaming

They compose naturally because they operate at different levels of the stack. In the current implementation, A2A, SSE, and OpenAI-compatible model calls are live runtime surfaces, while MCP remains a declaration-level boundary until runtime support lands.
