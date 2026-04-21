# Example: `me_agent`

This example shows a self-hosted agent that keeps its own `agent_space/` while optionally onboarding into a modern Foundry.

It is intentionally simple:

- local `agent_space/` files
- optional LLM call through OpenAI-compatible APIs
- safe fallback behavior when no model credentials are configured
- direct and inline chat support
- optional automatic Foundry onboarding with discovery, poll/push invite delivery, register, and approval
- explicit Foundry sandbox commands for demo purposes

## Run

```bash
cd ccfoundry-agent-kit
source .venv/bin/activate
uvicorn me_agent_example.app:app --app-dir examples/me_agent/src --reload --port 8085
```

## Environment

Copy `.env.example` to `.env` if you want real model calls.

If no model is configured, the example still works with deterministic local responses.

## Foundry onboarding

The demo can automatically talk to a modern Foundry:

1. the agent starts and exposes `/.well-known/agent-card.json`
2. the SDK announces the agent through `POST /api/registry/discover`
3. Foundry evaluates the discovery against its current requirements
4. admin issues an invite
5. Foundry pushes the one-time invite to `POST /foundry/bootstrap/invite` or exposes it via poll
6. the SDK auto-registers with `POST /api/registry/register`
7. after admin approval, Foundry pushes or exposes the approved secret, model policy, and resources

Minimum config:

```bash
FOUNDRY_DISCOVERY_ENABLE=true
FOUNDRY_BASE_URL=http://127.0.0.1:9090
FOUNDRY_AGENT_PUBLIC_URL=http://127.0.0.1:8085
FOUNDRY_BOOTSTRAP_DELIVERY=push
```

You can inspect the current bootstrap state from:

```bash
curl http://127.0.0.1:8085/foundry/bootstrap/state
```

If Foundry cannot call back into the local machine, switch to:

- `FOUNDRY_BOOTSTRAP_DELIVERY=poll`
- or `FOUNDRY_BOOTSTRAP_DELIVERY=hybrid`

You can also attach a short-lived developer claim token before re-discovering:

```bash
FOUNDRY_DISCOVERY_CLAIM_TOKEN=dev-claim-...
FOUNDRY_DEVELOPER_IDENTITY_JSON='{"github_login":"alice","repo":{"slug":"example/me_agent"}}'
```

When Foundry approves the agent, it can also provide:

- `LLM_API_KEY`
- `LLM_API_BASE`
- `LLM_MODEL`
- `LLM_ALLOWED_MODELS_JSON`

The example runtime now prefers Foundry's model policy over its local default config when those keys are present.

## Default external-agent contract

The bundled config already expresses the recommended secure default:

- `execution_mode = remote_brain_foundry_workspace`
- `agent_space_location = external`
- `workspace_location = foundry_sandbox`
- `skill_delivery_mode = descriptor_only`

In that mode:

- this example keeps its own `agent_space/`
- Foundry owns the task workspace
- task execution should happen through the Foundry sandbox control plane
- native PTY should stay disabled unless you deliberately choose a different trust model

## Using a Foundry sandbox after approval

After approval, the bootstrap state can contain:

- `env_vars.AGENT_SECRET`
- `allocated_resources.sandbox_workspace.control_plane`

Use the SDK helper:

```python
from me_agent_example.app import FOUNDRY_BOOTSTRAP

sandbox = FOUNDRY_BOOTSTRAP.sandbox_client()
await sandbox.start()
await sandbox.workspace_write("jobs/input.txt", "hello from ext agent")
payload = await sandbox.terminal_exec("cat jobs/input.txt")
print(payload["state"]["capture_text"])
await sandbox.stop()
```

## Demo sandbox commands

The example chat handler also supports a few explicit sandbox commands so the Foundry UI can demonstrate the control plane end to end:

- `run ls -la in the sandbox`
- `show sandbox workspace`
- `read sandbox file test.md`
- `write sandbox file test.md: hello`
