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

LAN mode is intended for trusted development networks only. The API can start local processes, proxy agent requests, request bootstrap tickets, and launch Cloud Run jobs. By default, API CORS allows localhost origins; the launcher widens this only for explicit LAN mode. To pin allowed origins yourself, set:

```bash
CCFOUNDRY_DEV_BOARD_ALLOWED_ORIGINS=http://192.168.1.10:3000
```

Its purpose is to make agent-side development and Foundry bootstrap debugging easy:

- create and manage agent sources
- run a local debug runtime and use the playground against that local process
- deploy a selected source agent to Google Cloud Run
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

If an existing agent was created before a resource-bearing skill copied its resources, reinstall that skill or copy the missing resource directory into the agent instance before running job execution.

## Cloud Run Deployment

The guided setup flow separates the **agent source** from the **runtime target**:

1. Create an agent source from a template, with an optional `Load skills` jump to Skill Store. This creates the durable workspace only; it does not have to start a local runtime.
2. Choose the runtime target: `Local debug runtime` or `Cloud Run pull worker`
3. Log in to Foundry with GitHub
4. Request the Foundry bootstrap claim
5. Wait for Foundry onboarding
6. Run the appropriate smoke test
7. Open Foundry to test the linked agent

Step 3 also lets the developer sync a bounty-success email preference to Foundry. The preference belongs to the developer identity, while the local file copy lives with the selected agent source for Dev Board convenience. Successful bounty settlement emails include completion status, resource costs, net payout, and Stripe references. See [Foundry Bounty Email Notifications](foundry-bounty-email-notifications.md).

When `Google Cloud Run` is selected, step 2 shows Cloud Run preflight, Google Cloud login, project/region settings, dry-run, and deploy controls. The region field has quick picks for `us-central1`, `europe-west2` (UK London), `asia-east2` (Hong Kong), and `asia-southeast1` (Singapore), while still accepting another valid Cloud Run region ID. The default poll schedule is `* * * * *`, which keeps demo and development agents responsive with one poll per minute.

The Cloud Run tab also includes a live worker inventory from the active Google Cloud project and region. This inventory is separate from the local source list, so a deployed worker whose source was removed locally is still visible as a source-missing Cloud Run runtime instead of silently continuing to poll Foundry.

The real deploy is intentionally gated until the Foundry claim is installed on the selected source. If a local debug runtime is running, Dev Board can apply the claim to that runtime immediately; for Cloud Run, Dev Board writes the claim to the source bootstrap state and the deployment passes it into the worker for the first poll. A local runtime is not required for the Cloud Run bootstrap path. Deployment can take a few minutes while Docker builds, pushes to Artifact Registry, Cloud Run creates a revision, and Cloud Scheduler is configured; the UI shows elapsed time plus the latest deployment logs. The Cloud Run smoke test checks deployment status, Scheduler, and the latest bootstrap poll instead of using the local playground chat path.

The local playground is only for local runtime debugging. Cloud Run workers are validated through deployment status, Scheduler, `/foundry/poll`, invocation results, and settlement records.

Earnings are tied to the Foundry-registered agent identity, not to where the runtime happened to execute. The Earnings view resolves the selected source agent's registered Foundry name and shows settlements from local debug runs and Cloud Run bounty runs under that same identity.

The `Agent card -> Cloud Run` tab exposes the same headless Cloud Run deploy flow from [Cloud Run Deployment](cloud-run-deployment.md). It checks local `gcloud` / Docker status, shows the active Google account and project, and starts an asynchronous deployment job for the selected agent.

The deployment job uses the selected agent instance's `agent_space` directory as the build input and calls:

```bash
scripts/deploy-cloudrun.sh <agent-name> --agent-space .dev-board/agents/<agent>/agent_space
```

Dev Board stores deployment job logs under its runtime directory and polls them from the UI. A dry run is available from the same panel; it prints the Docker and `gcloud` commands without pushing or deploying.

The Cloud Run flow expects Google Cloud authentication on the machine running the Dev Board API. This can be a user login from `gcloud auth login --no-launch-browser` or a GCE/GVM service account with sufficient Cloud Run, Artifact Registry, and Cloud Scheduler permissions. The UI surfaces the current auth status and provides a `Google Cloud login` action for the no-browser flow. Real deploy is disabled until `gcloud` is authenticated; dry-run remains available for command inspection.

No Google service account key files are required or committed. Cloud SDK user credentials remain in the host user's `gcloud` config directory, and Dev Board auth session/runtime logs live under ignored runtime paths.

## Retiring Agents

Use `Agent card -> Overview -> Retire agent` or the `Retire` action in `Agent card -> Local runtimes` to clean up Dev Board test agents. Retire requires the same GitHub developer login used for onboarding. Dev Board first asks Foundry to soft-retire the linked external agent, then stops the local runtime and removes it from active Dev Board inventories. The local runtime record remains in `.dev-board/local_agents.json` with status `retired` for audit/debugging, but `agents.yaml` and the default UI list hide it.

Retirement is modeled as a soft-retire request rather than a hard delete: historical tasks, settlements, and audit trails should remain with the host system, while active bindings are removed from normal picker lists. Restore or audit workflows belong in a host-side operator surface, not in this local Dev Board.

## Components

- `apps/agent-dev-board-api`
  A small FastAPI service that creates local agents from templates, proxies requests to configured local agents, talks to Foundry-side developer bootstrap endpoints, and launches Cloud Run deployment jobs.
- `apps/agent-dev-board-web`
  A React UI with top-level views for:
  - guided setup
  - agent card
  - local playground
  - earnings
  - skill store
  - job board

  Inside `agent card`, the current tabs are:
  - overview
  - agent sources
  - Cloud Run
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
- sync a developer bounty-success email preference to Foundry

In the current browser flow, GitHub is the only supported developer sign-in method.

## Non-goals

- multi-tenant RBAC
- production task orchestration
- production billing or payout enforcement
- payment processing
- cloud operations across a fleet of agents
- full Foundry operator surface

Those remain part of the Foundry control plane.

## Security Notes

Agent Dev Board is a local harness, not a production service. Keep the default loopback binding for normal work. Remote Foundry URLs should use HTTPS; non-loopback plain HTTP requires `CCFOUNDRY_ALLOW_INSECURE_REMOTE_HTTP=true`. Job proxy endpoints only forward to loopback HTTP agent URLs.

See [Security](security.md) for the full threat model and pre-publish checklist.

See also:

- [Architecture](architecture.md)
- [Foundry Onboarding](foundry-onboarding.md)
