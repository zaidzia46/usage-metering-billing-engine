# Build Log

How AI was used on this project, where it was wrong, and what I changed.

## How I worked

I built this with Claude as a  guide. Claude proposed each file and explained
it. I created every file myself, ran every command, and checked the results against what
was expected before moving on. I'm new to backend work, so I asked Claude to make the
technical decisions and explain the reasoning. Where I could verify something myself
(database rows, HTTP status codes, test results), I did.

## Where AI helped

- **Design:** the schema, the 402-vs-429 rule, the exact quota boundary, and the idea of
  enforcing idempotency with a database unique constraint plus a row lock instead of an
  `if exists` check.
- **Code:** all layers (routes, services, repositories), the pricing calculator, the Stripe
  Checkout and webhook handling, the reconciliation job, and the Alembic setup.
- **Tests:** the 45 tests, including signing fake Stripe events with the webhook secret so
  they run offline.
- **Teaching:** explanations of Docker, migrations, sessions, transactions, and locks.

## Where AI was wrong, and what we changed

1. **Webhook deduplication bug (the big one).** The first version of `claim_event` decided
   whether an event was new by checking `rowcount`. It was wrong: every event was treated as
   a duplicate, so the handler returned 200 and did nothing. My payment at Stripe went through,
   but my tenant stayed on Free. I noticed that `processed_webhook_events` was empty even
   though webhooks returned 200. A scratch script calling `claim_event` twice showed
   `first: False`. The fix was `INSERT ... ON CONFLICT DO NOTHING RETURNING`, which now
   returns `True` for a new event and `False` for a replay. Webhook tests cover it.
2. **Wrong first diagnosis of the database error.** When Python couldn't connect to
   Postgres ("password authentication failed"), Claude's first guess was a stale Docker
   volume. That did not fix it. The real cause was another Postgres on my PC using port 5432.
   I moved the Docker database to port 5433.
3. **A command that didn't work on my machine.** The `stripe listen --events a,b,c` line
   Claude gave me failed in PowerShell, because the unquoted commas turned the list into
   something else. The fix was to quote the list.
4. **A wrong claim about my data.** Claude told me tenant 1 had paid at Stripe while no
   listener was running. The reconciliation run found nothing to fix, so that was most likely
   wrong. I never completed a payment for tenant 1.
6. **Test counts.** Claude miscounted the expected number of tests twice (38 instead of 37,
   then 12 instead of 11 pricing tests). I compared against the real output each time.
7. **A design change made while coding.** `DESIGN.md` originally had one usage row per
   usage type. Two rows sharing one idempotency key would break the unique constraint, so one
   request is now one row holding both usage types. I updated `DESIGN.md` to match.
8. **A missed requirement.** Claude's first plan didn't include the background job, which the
   brief lists only in the shared requirements section. It was added later as the
   reconciliation job.

## Mistakes on my side

- I ran `stripe events resend` on the wrong events twice before checking the customer ID
  inside the event.
- I pasted a Stripe CLI webhook secret into the AI chat. It's a sandbox secret that only
  works against my own localhost, but it shouldn't have been shared. It was never committed.

## What I verified myself

- Ran each step and compared real output to the expected output.
- Checked the database directly for every claim (event rows, plan changes, constraints).
- Completed a real Stripe test-mode payment and watched the webhook upgrade a tenant.
- Hand-calculated the token pricing example (29,000 micro-dollars) before running the code.

## Known limitations

See the Limitations section of `README.md`.