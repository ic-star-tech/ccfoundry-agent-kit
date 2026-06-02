# Foundry Bounty Email Notifications

This document records the implementation plan and current wiring for bounty-success email notifications. The goal is:

1. A developer enters an email in Dev Board.
2. Dev Board syncs that preference to Foundry using the same developer bootstrap identity used for onboarding.
3. When a bounty settles successfully, Foundry records an email outbox item with completion status, resource cost, net payout, and Stripe references.
4. A mail provider can deliver the outbox item without blocking settlement.

## Phase 1: Preference Capture

Status: implemented in Agent Dev Board and Foundry.

Agent Dev Board changes:

- `GET /api/local-agents/{agent_name}/notification-preferences`
  reads the selected agent source's local `.foundry_notifications.json`.
- `POST /api/developer/notification-preferences/sync`
  sends the email preference to Foundry with `Authorization: Bearer <developer bootstrap token>` when available, or a trusted GitHub token for hosted Foundry targets.
- Guided setup step 3 now has a `Completion email` field, a `Bounty success emails` toggle, and a `Sync email` action.
- After a bootstrap ticket is requested, Dev Board auto-syncs the email preference when an email is present or the toggle disables an existing preference.
- The discovery claim now merges the full `developer_identity` returned by the Foundry bootstrap ticket so GitHub id/login keys stay aligned across agent binding, settlement, and notification preferences.

Foundry changes:

- New router: `server/routers/developer_notifications.py`
- New service: `server/services/email_notification_service.py`
- New endpoint: `POST /api/developer/notification-preferences`
- New endpoint: `GET /api/developer/notification-preferences`
- Authentication reuses `AgentDiscoveryService._authorize_bootstrap_ticket`, so the preference is bound to the same developer identity as agent onboarding.

The developer key follows the existing bounty settlement key convention:

```text
github:<github_id_or_login>
```

That matches the key derived from `foundry.developer_agent_bindings` during bounty settlement.

## Phase 2: Settlement Hook and Outbox

Status: implemented in Foundry.

New tables are initialized during Foundry startup:

- `foundry.developer_notification_preferences`
- `foundry.email_notification_outbox`

The bounty verification path now enqueues an email after:

- sandbox finalization and resource-cost accounting
- net payout calculation
- Stripe settlement attempt
- append-only accounting ledger write

The email enqueue is best-effort. If notification enqueue fails, bounty settlement still succeeds and the settlement result includes an `email_notification` error object for audit.

The outbox idempotency key is:

```text
bounty_success:<settlement_id>:<recipient_email>
```

This prevents duplicate emails for repeated settlement-result updates.

## Email Contents

The bounty-success email includes:

- requirement or bounty name
- requirement id
- agent name
- verification summary
- settlement id
- gross reward
- LLM cost
- sandbox cost
- other resource cost
- total resource cost
- net payout
- public settlement link
- Stripe payment / transfer dashboard link or Stripe reference id when available

The settlement link is based on `FOUNDRY_PUBLIC_BASE_URL`, defaulting to `https://foundry.cochiper.com`. It resolves to `/settlements/{settlement_id}`, a public Foundry page that reads `/api/public/settlements/{settlement_id}` and shows gross reward, resource costs, net payout, verification checks, and Stripe status or links.

## Phase 3: Delivery Provider

Status: provider abstraction implemented; real delivery is config-gated. The preferred production provider is Foundry's global `email-mcp`, which is already used by user registration approval/rejection email.

Environment variables:

```bash
EMAIL_NOTIFICATIONS_ENABLED=true
EMAIL_PROVIDER=mcp
EMAIL_MCP_NAME=email-mcp
EMAIL_MCP_TOOL=send_email
FOUNDRY_PUBLIC_BASE_URL=https://foundry.cochiper.com
FOUNDRY_STRIPE_DASHBOARD_BASE=https://dashboard.stripe.com/test/payments
```

`EMAIL_PROVIDER=mcp` calls `email-mcp.send_email` through Foundry `McpService.call_global_tool`.

`EMAIL_PROVIDER=console` logs the rendered message and marks the outbox row as sent when `EMAIL_NOTIFICATIONS_ENABLED=true`.

SMTP is also supported:

```bash
EMAIL_PROVIDER=smtp
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=...
SMTP_PASSWORD=...
SMTP_USE_TLS=true
EMAIL_FROM="CoChiper Foundry <no-reply@cochiper.com>"
```

If `EMAIL_NOTIFICATIONS_ENABLED` is not enabled, settlement only writes `PENDING` outbox rows. A later worker can drain those rows with the same service method.

## Phase 4: Follow-up Work

Recommended next steps:

- Add a background outbox worker with retry/backoff for `PENDING` and `FAILED` rows.
- Add an operator/admin view for email outbox status and resend.
- Add unsubscribe or preference-management links once external users rely on this channel.
- Add HTML branding after the plain text email is stable.

## Verification

Completed checks:

- Local Agent Dev Board API compile
- Agent Dev Board web build
- Foundry remote compile and targeted tests
- Foundry route registration sanity check confirmed `/api/developer/notification-preferences`
- Foundry startup confirmed `Email notification schema initialized`
- Public settlement page build and deploy
- Public smoke checks confirmed `/api/public/settlements/<settlement_id>` returns settlement data and the deployed bundle contains the `/settlements/:settlementId` page
