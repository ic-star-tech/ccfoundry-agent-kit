# Gitea vs GitHub Changelog

This document summarizes the features and implementation present in the private
Gitea repository that are **not yet reflected** in the public GitHub mirror.
These additions build on the open-source foundation to add production-grade
Google Cloud Run deployment, AP2-inspired agent payment settlement, and
Foundry resource-cost accounting.

> [!NOTE]
> The public GitHub repository remains the canonical open-source baseline. The
> Gitea-specific features documented here are expected to be upstreamed
> incrementally after stabilization and security review.

## Feature Groups

### 1. Google Cloud Run Deployment Pipeline

**Scope:** end-to-end serverless deployment of a CCFoundry agent to Cloud Run
with Cloud Scheduler auto-polling.

| Component | Files |
|-----------|-------|
| Multi-stage Dockerfile | `Dockerfile.cloudrun` |
| One-command deploy script | `scripts/deploy-cloudrun.sh` |
| Deploy & smoke-test docs | `docs/cloud-run-deployment.md` |
| Dev Board Cloud Run panel | `apps/agent-dev-board-api/src/agent_dev_board_api/cloud_run.py` |
| Dev Board Cloud Run UI | `apps/agent-dev-board-web/src/App.tsx` (Cloud Run tab) |

Key capabilities:

- Docker build → Artifact Registry push → Cloud Run deploy → Cloud Scheduler
  creation in a single command.
- Dev Board guided setup allows choosing `Google Cloud Run` as the runtime
  target, with Google account login, project/region selection, dry-run preview,
  and asynchronous deploy with streamed logs.
- Cloud Run panel shows a live worker inventory from the active Google Cloud
  project, including source-missing workers.
- Agent retirement from Dev Board also cleans up Cloud Scheduler jobs, Cloud Run
  services, and Artifact Registry images.
- `AGENT_DEPLOY_MODE=cloud_run` env var disables internal polling loops; a new
  `POST /foundry/poll` endpoint replaces them.
- Default region `europe-west2`, with quick picks for `us-central1`,
  `asia-east2`, and `asia-southeast1`.

### 2. Pull Runtime (Cloud Run Serverless Pattern)

**Scope:** replaces internal background loops with on-demand polling driven by
Cloud Scheduler or any external trigger.

| Component | Files |
|-----------|-------|
| Pull runtime class | `packages/python-sdk/src/ccfoundry_agent_kit/pull_runtime.py` |
| Bootstrap Cloud Run mode | `packages/python-sdk/src/ccfoundry_agent_kit/bootstrap.py` |

Key capabilities:

- `FoundryPullRuntime` claims tasks from Foundry via
  `POST /api/agent-runtime/claim` and processes `chat_turn` and
  `bounty_execute` invocations.
- `poll_once()` is designed for external triggers (Cloud Scheduler) — no
  persistent process required.
- When `AGENT_DEPLOY_MODE=cloud_run`, `start()` skips the internal `_run_loop`;
  likewise, `FoundryBootstrap.start()` skips `_heartbeat_loop`.
- Fully backward-compatible: without the env var, the agent behaves exactly as
  before with internal loops.

### 3. AP2-Inspired Payment Mandates

**Scope:** three-layer payment mandate chain modeled after [Google AP2 (Agent
Payments Protocol)](https://github.com/google-agentic-commerce/AP2) for
cryptographically signed agent settlement.

| Component | Files |
|-----------|-------|
| Mandate builders & HMAC signing | `packages/python-sdk/src/ccfoundry_agent_kit/mandate_signing.py` |
| Data models | `packages/python-sdk/src/ccfoundry_agent_kit/models.py` |
| Tests | `packages/python-sdk/tests/test_mandate_signing.py` |

Key capabilities:

- Three mandate types map to the Foundry agent labor market:
  - **IntentMandate** ≈ task brief with budget ceiling
  - **CartMandate** ≈ agent's quote / bid with fee breakdown
  - **SettlementMandate** ≈ signed proof of verified payment
- HMAC-SHA256 signing using the existing `AGENT_SECRET` as the shared key.
- `sign_mandate()` / `verify_mandate()` with constant-time comparison.
- SDK-exported helpers: `create_intent_mandate()`, `create_cart_mandate()`,
  `create_settlement_mandate()`.
- `FoundryMandate`, `MandateItem`, `SettlementRecord`, `SettlementNotification`
  Pydantic models.
- Bootstrap handles incoming `task_settled` actions: verifies the mandate
  signature and stores an audit trail in local state.

### 4. Resource Cost Accounting

**Scope:** per-task attribution of Foundry-provided LLM and sandbox costs, with
automatic deduction from bounty rewards before Stripe payout.

| Component | Files |
|-----------|-------|
| Billing context model | `packages/python-sdk/src/ccfoundry_agent_kit/models.py` (`BillingContext`, `SettlementBreakdown`) |
| LLM metadata helper | `packages/python-sdk/src/ccfoundry_agent_kit/llm_metadata.py` |
| Pull runtime billing | `packages/python-sdk/src/ccfoundry_agent_kit/pull_runtime.py` |
| Sandbox client billing | `packages/python-sdk/src/ccfoundry_agent_kit/sandbox_client.py` |
| Design plan | `docs/resource-cost-accounting-plan.md` |
| Implementation review | `docs/resource-cost-accounting-implementation-review.md` |

Key capabilities:

- `BillingContext` carries `invocation_id`, `requirement_id`, and `job_name`
  through the entire task lifecycle.
- `FoundrySandboxClient.start()` / `stop()` accept `invocation_id` and
  `billing_context` so sandbox runtime cost is attributed to the correct task.
- `foundry_llm_metadata()` helper produces safe LiteLLM metadata for Foundry
  resource-cost accounting — `foundry_invocation_id`,
  `foundry_requirement_id`, `foundry_agent_name`.
- Settlement formula: `net_payout = gross_reward - (llm_cost + sandbox_cost + feature_cost)`.
- If `net_payout == 0`, Stripe payout is skipped; `unrecovered_resource_cost_usd`
  is preserved for audit.
- The pull runtime enforces `invocation_id` alignment and passes billing context
  through the entire bounty execution → sandbox → deliverable submit chain.
- Verified E2E with Cloud Run agents `test11` and `v1_agent_ext` including real
  Stripe PaymentIntents and sandbox cost deduction at `$4/hour` runtime rate.

### 5. Bounty Email Notifications

**Scope:** notify agent developers by email when a bounty settles successfully.

| Component | Files |
|-----------|-------|
| Specification & status | `docs/foundry-bounty-email-notifications.md` |
| Dev Board notification sync | `apps/agent-dev-board-api/src/agent_dev_board_api/app.py` |
| Dev Board UI (guided setup) | `apps/agent-dev-board-web/src/App.tsx` |

Key capabilities:

- Developer enters an email in Dev Board guided setup → synced to Foundry.
- On bounty settlement, Foundry enqueues an email with gross reward, resource
  costs, net payout, and Stripe references.
- Email contains public settlement link and optionally a Stripe dashboard link.
- Delivery providers: `mcp` (Foundry `email-mcp`), `smtp`, or `console`.
- Idempotent outbox keyed by `bounty_success:<settlement_id>:<email>`.
- Best-effort: email failure never blocks settlement.

### 6. Earnings & Settlement Inspection

**Scope:** Dev Board Earnings panel and settlement history.

Key capabilities:

- Dev Board shows `Earnings` tab with settlement list, aggregated totals, and
  gross / resource cost / net breakdown per settlement.
- Settlements are fetched from Foundry by the agent's registered Foundry
  identity — works for both local and Cloud Run bounty runs.
- Settlement records include `stripe_payment_intent_id`,
  `stripe_transfer_id`, mandate verification status, and resource cost items.

### 7. SDK Module Additions

| Module | Purpose |
|--------|---------|
| `mandate_signing.py` | AP2 mandate builders and HMAC signing |
| `llm_metadata.py` | Foundry billing context → LiteLLM metadata |
| `task_tracker.py` | Markdown-based personal task board with recurring schedules |
| `pull_runtime.py` | Foundry pull-based task claim and execution |

### 8. Documentation Additions

| Document | Summary |
|----------|---------|
| `docs/cloud-run-deployment.md` | Full Cloud Run deployment guide |
| `docs/resource-cost-accounting-plan.md` | 5-phase implementation plan for resource cost deduction |
| `docs/resource-cost-accounting-implementation-review.md` | Implementation status and E2E verification log |
| `docs/foundry-bounty-email-notifications.md` | Bounty email notification pipeline |
| `docs/agent-dev-board.md` (expanded) | Cloud Run panels, email sync, source identity |

## Summary of Exports Added to SDK `__init__.py`

```python
# Payment & settlement
FoundryMandate, MandateItem, SettlementRecord, SettlementNotification
BillingContext, SettlementBreakdown
create_intent_mandate, create_cart_mandate, create_settlement_mandate
sign_mandate, verify_mandate
foundry_llm_metadata

# Pull runtime
FoundryPullRuntime

# Task management
TaskTracker
```

## Configuration Changes

| Variable | Where | Purpose |
|----------|-------|---------|
| `AGENT_DEPLOY_MODE` | Cloud Run | Set to `cloud_run` to disable internal loops |
| `ME_AGENT_BASE_DIR` | Cloud Run | Path to the agent's base directory |
| `FOUNDRY_RUNTIME_TRANSPORT` | Cloud Run / SDK | Set to `pull` for Cloud Run |
| `FOUNDRY_AGENT_PUBLIC_URL` | Cloud Run | Auto-inferred from service URL |
| `FOUNDRY_BOOTSTRAP_DELIVERY` | Cloud Run | Set to `poll` for serverless |
| `FOUNDRY_AGENT_SOURCE_ID` | Dev Board / Cloud Run | Durable agent source identity |
| `FOUNDRY_REGISTERED_AGENT_NAME` | Cloud Run (env seed) | Restore approved state on ephemeral filesystem |
| `FOUNDRY_REGISTRATION_STATUS` | Cloud Run (env seed) | Restore approved state |
| `FOUNDRY_APPROVED_AT` | Cloud Run (env seed) | Restore approved state |
| `FOUNDRY_ALLOCATED_RESOURCES_JSON` | Cloud Run (env seed) | Restore resource contracts |
