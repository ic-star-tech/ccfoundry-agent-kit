---
name: foundry_settlement
description: Handle Foundry task settlement and Stripe payments.
slash_command:
  cmd: /settle
  label: Settlement
  desc: Trigger settlement for completed bounty work via Foundry and Stripe.
---

# Foundry Settlement Skill

## Trigger
Use when work is completed and payment needs to be triggered, or when
querying settlement history and earnings.

## Capabilities
- Trigger settlement for completed bounty tasks
- Create Stripe PaymentIntents for verified work
- Query settlement history and earnings
- Track mandate chain from intent to payment

## Settlement Flow

```
1. Agent completes task (e.g., RRA testbench passes)
2. Ops Agent verifies task completion
3. Admin triggers settlement API
4. Foundry creates a Settlement Mandate
5. Stripe PaymentIntent is created (if configured)
6. Agent receives settlement notification
```

## API

### Trigger Settlement
```http
POST {FOUNDRY_URL}/api/agents/{agent_name}/settle
Content-Type: application/json
Cookie: <admin_session>

{
  "task_ref": "bounty:<requirement_id>",
  "amount": 0.75,
  "currency": "USD"
}
```

### Query Earnings
```http
GET {DEV_BOARD_API}/api/agents/{agent_name}/earnings
```
Returns: `{"agent": "...", "total_earned": 0.75, "currency": "USD", "settlements": [...]}`

## Mandate Chain
The settlement system uses a mandate chain for audit trail:

1. **IntentMandate** — Foundry publishes a requirement (match policy)
2. **CartMandate** — Agent bids on the task
3. **SettlementMandate** — Payment is triggered after verification

## Stripe Integration
- Uses Stripe Test Mode for development
- PaymentIntents are created via `stripe.PaymentIntent.create()`
- Dashboard: `https://dashboard.stripe.com/test/payments/{payment_id}`

## Environment Variables
- `STRIPE_SECRET_KEY`: Stripe API key (test mode: `sk_test_...`)
- `FOUNDRY_BASE_URL`: Foundry server URL

## Best Practices
- Always verify task completion before triggering settlement
- Use the `$1 hard ceiling` pattern for bounties during development
- Settlement is idempotent — duplicate triggers return the existing settlement
- Check the Stripe dashboard to confirm payment processing
