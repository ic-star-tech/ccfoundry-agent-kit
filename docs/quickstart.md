# Quickstart

This guide brings up the local `Agent Dev Board`, then creates and runs a template-based local agent inside the board.

## Install (Recommended)

Runtime: Python `3.10+` and Node.js `18+`. Node.js `20` is recommended.

From any empty working directory:

```bash
mkdir my-agent-dev-board
cd my-agent-dev-board
npm install -g ccfoundry@latest
ccfoundry
```

That command sequence will:

- install the global `ccfoundry` CLI
- create or reuse `.venv` in your current directory
- create a local runtime registry under `.dev-board/`
- install the editable Python packages from the published package bundle
- install the web UI dependencies when needed
- start the Dev Board API and web UI

For LAN testing from another device on the same network:

```bash
ccfoundry dev-board --host 0.0.0.0
```

`ccfoundry` by itself defaults to `ccfoundry dev-board`, and `ccfoundry agent-dev-board` is accepted as a longer alias.

## Run From Source

From the repository root:

```bash
npm run dev-board
```

That one command will:

- create or reuse `.venv`
- install the editable Python packages
- install the web UI dependencies when needed
- start the Dev Board API on `8090`
- start the Dev Board web UI on `3000`
- create a local runtime registry under `.dev-board/`
- let the browser create named local agents from template, each with its own port, logs, workspace, and bootstrap state

If one of those ports is already occupied, the launcher will automatically move that service to the next free port and print the final addresses.

For LAN testing from another device on the same network:

```bash
npm run dev-board:lan
```

That uses `0.0.0.0` for the board processes.

## First run inside the browser

After `ccfoundry` or `npm run dev-board` prints the Web URL:

1. open `Agent Dev Board`
2. stay on the default `Guided setup` page
3. create an agent from `Me Agent Template`
4. choose a stable name such as `my_agent_dev`
5. log in with GitHub and request a bootstrap ticket
6. let the guide watch the Foundry onboarding flow
7. open the playground for the final smoke test

The current Dev Board sign-in flow is GitHub-only. In the browser UI, developer bootstrap starts from the Foundry-hosted `Login with GitHub` popup.

## Prerequisites

- Python `3.10+`
- Node.js `18+`
- Node.js `20` recommended for the web UI

## Python setup

```bash
cd ccfoundry-agent-kit
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e packages/python-sdk
pip install -e examples/me_agent
pip install -e apps/agent-dev-board-api
```

## Run the example agent

```bash
uvicorn me_agent_example.app:app --app-dir examples/me_agent/src --reload --port 8085
```

Health check:

```bash
curl http://127.0.0.1:8085/health
```

## Run Agent Dev Board API

```bash
uvicorn agent_dev_board_api.app:app --app-dir apps/agent-dev-board-api/src --reload --port 8090
```

Health check:

```bash
curl http://127.0.0.1:8090/health
```

## Run Agent Dev Board Web

```bash
cd apps/agent-dev-board-web
npm install
npm run dev
```

Open the URL printed by Vite, usually `http://127.0.0.1:5173`.

The web app uses the current browser hostname to reach the API by default, so local and LAN testing can use the same build when the API is reachable on port `8090`.

What the board is good for:

- direct vs inline chat smoke tests
- temporary `model / base_url / api_key` overrides
- Foundry handshake probes
- developer bootstrap ticket requests
- local git and GitHub context inspection

## Optional: Connect To A Modern Foundry

Any local agent created from the template can also auto-onboard into a modern Foundry.

Minimum environment:

```bash
FOUNDRY_DISCOVERY_ENABLE=true
FOUNDRY_BASE_URL=http://127.0.0.1:9090
FOUNDRY_AGENT_PUBLIC_URL=http://127.0.0.1:8085
```

Then start the example agent as usual, or let `Agent Dev Board` create a local agent and set the same values for you:

```bash
uvicorn me_agent_example.app:app --app-dir examples/me_agent/src --reload --port 8085
```

The agent will:

1. send `POST /api/registry/discover`
2. keep the discovery alive with heartbeat
3. wait for an invite callback or poll for pending bootstrap actions
4. auto-register after invite issuance
5. wait for admin approval
6. persist the approved secret, model policy, and allocated resources in `.foundry_bootstrap.json`

Check the bootstrap state:

```bash
curl http://127.0.0.1:8085/foundry/bootstrap/state
```

If Foundry grants a sandbox-backed workspace, the approval payload will include:

- `allocated_resources.sandbox_workspace`
- `allocated_resources.sandbox_workspace.control_plane`

That control plane is meant to be used with `FoundrySandboxClient`.

## Optional: Developer bootstrap from Agent Dev Board

The current board flow connects directly to the real `https://foundry.cochiper.com` onboarding path from the `Guided setup` page.

1. open `Guided setup`
2. keep the Foundry URL set to `https://foundry.cochiper.com` unless you are targeting another compatible Foundry
3. click `Login with GitHub`
4. request a bootstrap ticket for the selected local agent
5. let the board install a `discovery_claim_token`, Foundry URL, and public base URL into that agent
6. let the agent re-discover with `bootstrap_delivery = poll` or `hybrid`

In the current UI, GitHub is the only supported developer sign-in method for this flow.
