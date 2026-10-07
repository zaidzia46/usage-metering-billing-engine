# Usage Metering & Billing Engine

A backend service that answers the three questions every SaaS must answer:
how much has this customer used, what does it cost, and have they hit their limit?

- **Metering** with exactly-once guarantees (idempotency keys, enforced by the database)
- **Quota enforcement** with exact boundary rules and clear 402 / 429 responses
- **Cost calculation** in integer micro-dollars, with cached-input and reasoning-token pricing rules
- **Stripe subscriptions** (test mode only) with signature-verified, deduplicated webhooks
- **Reconciliation job** that repairs any drift between our database and Stripe

Built with Python, FastAPI, PostgreSQL, SQLAlchemy, Alembic, and the Stripe Python SDK.

## Architecture

```
Client
  |
  |  POST /generate  (X-Tenant-Id, Idempotency-Key)
  v
routes/  ->  services/metering  ->  repositories/usage_repo  ->  PostgreSQL
  |             |  1. lock tenant row (SELECT ... FOR UPDATE)
  |             |  2. same key already recorded? -> return the original result
  |             |  3. subscription ok? usage + request <= plan limit?
  |             |        no -> 402 (Free plan / payment problem) or 429 (Pro plan)
  |             |  4. insert usage_event with cost, commit
  |
  |  GET /usage  ->  rollup of this month's usage_events -> { used, limit, remaining, cost }
  |
  |  POST /checkout  ->  Stripe Checkout (test mode)  ->  customer pays
  |
Stripe --signed webhook--> POST /webhooks/stripe
                              1. verify signature on the raw body (forged -> 400)
                              2. claim the event id (replay -> "duplicate", nothing changes)
                              3. update tenant plan / status, in the same transaction

Scheduled job:  python -m app.jobs.reconcile
                compares every tenant with Stripe and repairs any difference
```

Code is split in three layers: **routes** (HTTP only), **services** (business rules),
**repositories** (database queries only).

## Plans

| Plan | API calls / month | AI tokens / month |
|------|-------------------|-------------------|
| Free | 1,000             | 100,000           |
| Pro  | 50,000            | 5,000,000         |

Usage periods are calendar months in UTC.

## Rules worth knowing

- **Boundary:** a request is allowed if `used + requested <= limit`. At 999/1,000 a one-call
  request succeeds; at 1,000/1,000 the next one is refused.
- **402 vs 429:** a Free tenant over its limit gets **402** (upgrade needed). A Pro tenant
  over its limit gets **429** with a `Retry-After` header (seconds until the month resets).
  A tenant whose subscription is `past_due` or `unpaid` gets **402**.
- **Retries:** the retry check runs before the quota check, so a retried request that
  already succeeded gets its original response, even if the tenant is now at the limit.
  A retry returns the same body plus an `Idempotent-Replayed: true` header.
- **Key reuse:** the same `Idempotency-Key` with a different body returns **409**.
- **Token pricing:** `input_tokens` includes the cached part; `output_tokens` excludes
  reasoning tokens, which are billed at the output price. Quota tokens =
  input + output + reasoning (cached tokens are not counted twice). Costs are summed
  exactly and rounded once, at the end.

Prices live in `app/pricing_config.py` (illustrative values, not a real provider's):

| Item | Price |
|------|-------|
| Fresh input | $1.00 / 1M tokens |
| Cached input | $0.25 / 1M tokens |
| Output and reasoning | $4.00 / 1M tokens |
| API call | $0.002 |

## Setup

Requires Python 3.12, Docker, and a free Stripe account in **test mode**.

```bash
git clone https://github.com/zaidzia46/usage-metering-billing-engine && cd https://github.com/zaidzia46/usage-metering-billing-engine
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env              # Windows: copy .env.example .env
```

Edit `.env`: set a database password (the same value in `POSTGRES_PASSWORD` and inside
`DATABASE_URL`) and add your Stripe **test** values:

- `STRIPE_SECRET_KEY`: the `sk_test_...` key
- `STRIPE_PRO_PRICE_ID`: the `price_...` ID (not the `prod_...` ID) of a recurring monthly price
- `STRIPE_WEBHOOK_SECRET`: the `whsec_...` value printed by `stripe listen` (below)

Never use live keys. `.env` is git-ignored.

### Run

```bash
docker compose up -d --wait db
alembic upgrade head
uvicorn app.main:app --port 8000
```

### Seed demo data

```bash
python -m app.seed
```

Creates the Free and Pro plans and two demo tenants (ids 1 and 2, both on Free).
Safe to run more than once.

### Forward Stripe webhooks locally

In another terminal, with the Stripe CLI installed and logged in:

```bash
stripe listen --events "checkout.session.completed,customer.subscription.updated,customer.subscription.deleted" --forward-to localhost:8000/webhooks/stripe
```

Copy the `whsec_...` it prints into `.env` and restart the server.

### Try it

```bash
# Meter a request (send it twice: one usage event, second response flagged as a replay)
curl -i -X POST localhost:8000/generate \
  -H "Content-Type: application/json" -H "X-Tenant-Id: 1" -H "Idempotency-Key: demo-1" \
  -d '{"input_tokens": 1000, "output_tokens": 500}'

# See usage, limits and cost
curl localhost:8000/usage -H "X-Tenant-Id: 1"

# Start an upgrade: open the returned URL, pay with test card 4242 4242 4242 4242
curl -X POST localhost:8000/checkout -H "X-Tenant-Id: 1"
```

Interactive API docs: http://localhost:8000/docs

### Tests

```bash
docker compose up -d --wait db
python -m pytest -q
```

Tests use a separate `billing_test` database (created automatically, built from the real
migrations), so they never touch demo data. Webhook tests sign events with the webhook
secret from `.env`, so they need no Stripe account access.

### Reconciliation job

```bash
python -m app.jobs.reconcile
```

Run it on a schedule (cron or Windows Task Scheduler). Each tenant is retried up to 3 times
with backoff. If any tenant still fails, it logs an `ALERT` line and exits with code 1.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/generate` | Meter one billable request (needs `X-Tenant-Id`, `Idempotency-Key`) |
| GET | `/usage` | This month's usage, limits, remaining and cost |
| POST | `/checkout` | Create a Stripe Checkout session for the Pro plan |
| POST | `/webhooks/stripe` | Signature-verified Stripe webhook receiver |
| GET | `/health` | Liveness check |

## Limitations

Stated honestly, so nobody has to discover them:

- **No real authentication.** Tenants are identified by an `X-Tenant-Id` header, so anyone
  can claim any tenant. A real system would use per-tenant API keys.
- **Event ordering.** Stripe does not guarantee delivery order, so a stale event arriving
  late could briefly set the wrong plan. The reconciliation job repairs this on its next run.
- **Quota months vs billing cycles.** Quotas reset on the calendar month (UTC), not on each
  customer's Stripe billing date.
- **One subscription per tenant.** A tenant has a single subscription row, which is updated
  on re-subscribe.
- **Alerts are logs plus an exit code.** There is no email or Slack notification.
- **The reconciliation job must be scheduled by you.** It does not schedule itself.
- **Not built:** invoicing, proration, overage billing, usage alerts.
- **Stripe test mode only.** `/checkout` needs your own Stripe test keys to work.
- **Pricing values are illustrative**, not any real provider's prices.
- **Placeholder pages.** `/checkout/success` and `/checkout/cancel` are minimal stubs; the
  plan change happens through the webhook, never through the redirect.