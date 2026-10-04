# Design: Usage Metering & Billing Engine

## Problem
Answer three questions per tenant: how much have they used, what does it cost,
and have they hit their plan limit?

## Plans
| Plan | API calls / month | AI tokens / month |
|------|-------------------|-------------------|
| Free | 1,000             | 100,000           |
| Pro  | 50,000            | 5,000,000         |

## Data model
- tenants(id, name, plan_id, subscription_status, stripe_customer_id, created_at)
- plans(id, name, api_call_limit, token_limit)
- subscriptions(id, tenant_id, stripe_subscription_id, status, current_period_end)
- usage_events(id, tenant_id, type, quantity, cost_micros, idempotency_key, created_at)
  - UNIQUE(tenant_id, idempotency_key)
- processed_webhook_events(stripe_event_id PRIMARY KEY, processed_at)

All money is stored as integers in micro-dollars (1 USD = 1,000,000). No floats.

## API
- POST /generate    (header: Idempotency-Key) -> meter + quota check + cost
- GET  /usage       -> { used, limit, cost } for the current month
- POST /checkout    -> creates a Stripe Checkout session (Pro)
- POST /webhooks/stripe -> signature-verified, deduplicated

## Idempotency strategy
The database enforces it, not application code. The UNIQUE(tenant_id,
idempotency_key) constraint means a duplicate insert fails. On conflict, we
return the stored result of the original request instead of creating a new event.

## Quota rule (boundary)
A request is allowed if used + requested <= limit. At 999/1000 a 1-call request
is allowed, at 1000/1000 the next one is rejected.
- Free plan over limit -> 402 (upgrade needed)
- Pro plan over limit -> 429 with Retry-After (wait for month reset)
- Subscription past_due / canceled -> 402
The quota check and the insert run in one transaction with the tenant row locked
(SELECT ... FOR UPDATE), so two concurrent requests can't both slip under the limit.

## Layers
routes (HTTP) -> services (business logic) -> repositories (database)

## Non-goal
No invoicing, proration, or overage billing in the core.