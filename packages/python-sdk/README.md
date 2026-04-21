# `ccfoundry-agent-kit` Python SDK

The Python SDK is the agent-facing part of the stack. It helps a self-hosted agent:

- expose a compact runtime over FastAPI
- publish an A2A-style `Agent Card`
- participate in the modern Foundry onboarding flow
- receive approval callbacks or polled bootstrap actions
- install developer-claim payloads during local onboarding
- receive runtime model policy and resources
- consume a Foundry-provided sandbox control plane when the execution contract requires `foundry_sandbox`

## Main exports

- `AgentManifest`
- `ChatRequest` / `ChatResponse`
- `create_agent_app(...)`
- `AgentSpace`
- `FoundryBootstrap`
- `FoundrySandboxClient`

## Runtime surfaces

`create_agent_app(...)` always gives you:

- `GET /health`
- `GET /manifest`
- `POST /chat`
- `POST /api/chat`
- `GET /.well-known/agent-card.json`

When `FoundryBootstrap` is enabled, the app also exposes:

- `GET /foundry/bootstrap/state`
- `POST /foundry/bootstrap/invite`
- `POST /foundry/bootstrap/approved`
- `POST /foundry/bootstrap/developer-claim`

That developer-claim payload can carry:

- `discovery_claim_token`
- `bootstrap_delivery`
- `foundry_base_url`
- `public_base_url`

This makes it possible for a local tool such as `Agent Dev Board` to attach a claim to an already-running process without requiring the Foundry URL to be baked into the initial environment.

## Modern Foundry flow

The current onboarding flow is:

1. the agent announces itself to `POST /api/registry/discover`
2. Foundry matches the discovery against its own requirements and may run master-agent review
3. admin issues an invite
4. Foundry pushes the one-time invite to `/foundry/bootstrap/invite` or exposes it via poll
5. the agent auto-registers with `POST /api/registry/register`
6. admin approves
7. Foundry pushes or polls `AGENT_SECRET`, environment, model policy, and resource contracts

For the default external execution contract, the security boundary is:

- `agent_space_location = external`
- `workspace_location = foundry_sandbox`
- `skill_delivery_mode = descriptor_only`

That means the agent keeps its long-lived `agent_space`, while task files and shell execution happen inside a Foundry-managed sandbox.

## Foundry sandbox hands

After approval, the bootstrap state may include:

- `allocated_resources.sandbox_workspace`
- `allocated_resources.sandbox_workspace.control_plane`

Use `FoundrySandboxClient` to operate that workspace:

```python
sandbox = bootstrap.sandbox_client()
await sandbox.start()
await sandbox.workspace_write("jobs/hello.txt", "hello from ext brain")
result = await sandbox.terminal_exec("cat jobs/hello.txt")
print(result["state"]["capture_text"])
await sandbox.stop()
```

See:

- [SDK Guide](../../docs/sdk.md)
- [Foundry Onboarding](../../docs/foundry-onboarding.md)
