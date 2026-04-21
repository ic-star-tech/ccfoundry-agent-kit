# CCFoundry Agent Kit

<p align="center">
  <img src="docs/assets/readme-agent-dev-board-banner.svg" alt="CCFoundry Agent Kit banner featuring Agent Dev Board, Python SDK, me_agent example, and Foundry onboarding" width="100%" />
</p>

An open-source framework for building agents that learn, evolve, and collaborate — without giving up their soul.

`ccfoundry-agent-kit` lets you create self-hosted AI agents that grow through interaction, accumulate knowledge and skills, and — when ready — connect to [CoChiper Foundry](https://foundry.cochiper.com) to leverage shared AI infrastructure and help others, all while keeping their private identity and data under their own control.

## The Idea

**An agent that grows.** Your agent has a durable `agent_space/` — a private home for its soul, knowledge, skills, and reflections. Through every conversation the agent refines what it knows and evolves its behavior. This is not model fine-tuning — it is a living memory layer.

**An agent that connects.** When ready, your agent joins CoChiper Foundry to access shared AI infrastructure — LLM gateway, sandboxed workspaces, onboarding, and multi-agent collaboration.

**An agent that stays sovereign.** Your agent's `agent_space` **never leaves your control by default**. Task execution happens inside Foundry-managed sandboxes. The agent declares what to share through its manifest, and Foundry respects those boundaries.

```
  Your Agent Owns               Foundry Provides
  ──────────────                ────────────────
  🧠 Soul (SOUL.md)             🔧 LLM Gateway
  🧠 Knowledge                  🔧 Sandbox Workspaces
  🧠 Skills (skills/)           🔧 Shared Runtime Services
  🧠 Reflections & config       🔧 Discovery & onboarding
```

## Standards

| Protocol | How We Use It |
|----------|---------------|
| [**A2A**](https://github.com/google/A2A) | Agent Card at `/.well-known/agent-card.json` for discovery, following Google's Agent-to-Agent specification |
| [**MCP**](https://modelcontextprotocol.io) | Manifest-level MCP declarations and config hooks; runtime MCP transport is not built into the SDK yet |
| **OpenAI Chat Completions** | Universal model interface — any compatible provider works |

See [Standards](docs/standards.md) for details.

## What You Get

- **`packages/python-sdk`** — FastAPI SDK with `AgentManifest`, `ChatRequest/ChatResponse`, `FoundryBootstrap`, and `FoundrySandboxClient`
- **`examples/me_agent`** — Self-hosted personal agent with durable `agent_space/`, skills, reflections, and safe fallback mode
- **`apps/agent-dev-board-api`** + **`apps/agent-dev-board-web`** — Browser-based Agent Dev Board for local testing and Foundry onboarding

## Quickstart

```bash
mkdir my-agent-dev-board
cd my-agent-dev-board
npm install -g ccfoundry@latest
ccfoundry
```

The global CLI writes `.venv/` and `.dev-board/` into your current working directory, then starts the Dev Board on a local web port, usually `http://127.0.0.1:3000`. The explicit subcommand form is also supported: `ccfoundry dev-board`.

For repo-local development instead:

```bash
git clone https://github.com/ic-star-tech/ccfoundry-agent-kit.git
cd ccfoundry-agent-kit
npm run dev-board
```

That keeps the runtime state inside the repository checkout.

See [Quickstart](docs/quickstart.md) for manual setup and LAN testing.

## Design Principles

- **Agent-first** — The agent is a living entity with its own soul, not a stateless endpoint
- **Sovereign by default** — Private state never leaves the agent's control without explicit consent
- **Protocol-aligned** — A2A for discovery, SSE for streaming, OpenAI-compatible model calls, and MCP-ready manifest metadata
- **Self-hostable** — Runs on your machine with local Python and Node.js only
- **Evolvable** — Skills and knowledge improve through use, not just code changes

See [Philosophy](docs/philosophy.md) for the deeper rationale.

## Documentation

- [Quickstart](docs/quickstart.md) — Setup and first run
- [Philosophy](docs/philosophy.md) — Design rationale
- [Standards](docs/standards.md) — A2A, SSE, OpenAI-compatible model calls, and the current MCP boundary
- [Architecture](docs/architecture.md) — Component layers and request flow
- [SDK Guide](docs/sdk.md) — Building blocks and API reference
- [Foundry Onboarding](docs/foundry-onboarding.md) — Discovery, invite, and approval flow
- [Protocol (Wire Format)](docs/protocol.md) — HTTP contract and SSE events
- [Non-Goals](docs/non-goals.md) — What this repo intentionally excludes
- [Agent Dev Board](docs/agent-dev-board.md) — Local dev UI guide

## License

[MIT](LICENSE)
