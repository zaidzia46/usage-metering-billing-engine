import logging
import sys
import time

import stripe
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import Tenant
from app.repo import tenant_repo
from app.services import webhooks

stripe.api_key = settings.stripe_secret_key
stripe.max_network_retries = 2

logger = logging.getLogger("reconcile")

MAX_ATTEMPTS = 3
# Lower rank wins: a paid subscription beats a payment problem beats anything else.
STATUS_RANK = {"active": 0, "trialing": 0, "past_due": 1, "unpaid": 1}


def _pick_best(subs: list[dict]) -> dict:
    return sorted(subs, key=lambda s: (STATUS_RANK.get(s["status"], 2), -s["created"]))[0]


def reconcile_tenant(db: Session, tenant_id: int, customer_id: str) -> bool:
    """Make one tenant match Stripe. Returns True if something had drifted."""
    # Ask Stripe BEFORE taking the lock, so we never hold a lock during a network call.
    subs = [
        s.to_dict()
        for s in stripe.Subscription.list(customer=customer_id, status="all", limit=10).data
    ]

    tenant = tenant_repo.get_for_update(db, tenant_id)
    before = (tenant.plan.name, tenant.subscription_status)

    if subs:
        best = _pick_best(subs)
        webhooks._apply_subscription(
            db, tenant, best["id"], best["status"], webhooks._period_end(best)
        )
    elif tenant.plan.name == "pro":
        tenant.plan = webhooks._plan(db, "free")  # Stripe has nothing, so downgrade
        tenant.subscription_status = "active"

    after = (tenant.plan.name, tenant.subscription_status)
    db.commit()

    if before != after:
        logger.warning("Fixed drift for tenant %s: %s -> %s", tenant_id, before, after)
        return True
    return False


def run_once() -> tuple[int, int]:
    """Reconcile every tenant that has a Stripe customer. Returns (fixed, failed)."""
    with SessionLocal() as db:
        rows = db.execute(
            select(Tenant.id, Tenant.stripe_customer_id).where(
                Tenant.stripe_customer_id.is_not(None)
            )
        ).all()

    fixed = failed = 0
    for tenant_id, customer_id in rows:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                with SessionLocal() as db:  # a fresh session for every attempt
                    fixed += reconcile_tenant(db, tenant_id, customer_id)
                break
            except Exception:
                if attempt == MAX_ATTEMPTS:
                    failed += 1
                    logger.exception("Gave up on tenant %s after %d attempts", tenant_id, attempt)
                else:
                    time.sleep(2**attempt)  # wait 2s, then 4s, between attempts
    return fixed, failed


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    fixed, failed = run_once()
    logger.info("Reconciliation finished: %d fixed, %d failed", fixed, failed)
    if failed:
        logger.error("ALERT: reconciliation failed for %d tenant(s)", failed)
        sys.exit(1)  # a non-zero exit code is how a scheduler knows the job failed


if __name__ == "__main__":
    main()