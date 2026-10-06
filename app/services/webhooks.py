import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.repo import tenant_repo, webhook_repo

logger = logging.getLogger(__name__)

PAID_STATUSES = {"active", "trialing"}
PAYMENT_PROBLEM_STATUSES = {"past_due", "unpaid"}
ENDED_STATUSES = {"canceled", "incomplete_expired"}


def handle_event(db: Session, event: dict) -> str:
    """Process one verified Stripe event. Returns 'processed', 'duplicate' or 'ignored'."""
    try:
        # First, claim the event ID. A replay stops here.
        if not webhook_repo.claim_event(db, event["id"]):
            db.rollback()
            return "duplicate"

        handler = HANDLERS.get(event["type"])
        if handler is None:
            db.commit()
            return "ignored"

        handler(db, event["data"]["object"])
        db.commit()  # saves the claim AND the plan change together
        return "processed"
    except Exception:
        db.rollback()  # undoes the claim too, so Stripe's retry gets a fresh chance
        raise


def _on_checkout_completed(db: Session, session: dict) -> None:
    if session.get("mode") != "subscription" or not session.get("subscription"):
        return
    if session.get("payment_status") not in ("paid", "no_payment_required"):
        return  # not paid yet, a later subscription event will tell us

    tenant = _find_and_lock_tenant(db, session.get("customer"))
    if tenant is None:
        return
    _apply_subscription(db, tenant, session["subscription"], "active", None)


def _on_subscription_changed(db: Session, sub: dict) -> None:
    tenant = _find_and_lock_tenant(db, sub.get("customer"))
    if tenant is None:
        return
    _apply_subscription(
        db, tenant, sub["id"], sub["status"], _period_end(sub)
    )


def _on_subscription_deleted(db: Session, sub: dict) -> None:
    tenant = _find_and_lock_tenant(db, sub.get("customer"))
    if tenant is None:
        return
    _apply_subscription(db, tenant, sub["id"], "canceled", _period_end(sub))


HANDLERS = {
    "checkout.session.completed": _on_checkout_completed,
    "customer.subscription.updated": _on_subscription_changed,
    "customer.subscription.deleted": _on_subscription_deleted,
}


def _find_and_lock_tenant(db: Session, customer_id: str | None):
    if not customer_id:
        return None
    tenant = tenant_repo.get_by_stripe_customer_id(db, customer_id)
    if tenant is None:
        logger.warning("Webhook for unknown Stripe customer %s, ignoring", customer_id)
        return None
    # Lock the row, so two webhooks for one tenant are handled one at a time.
    return tenant_repo.get_for_update(db, tenant.id)


def _apply_subscription(db, tenant, stripe_sub_id, status, period_end) -> None:
    webhook_repo.upsert_subscription(db, tenant.id, stripe_sub_id, status, period_end)

    if status in PAID_STATUSES:
        tenant.plan = _plan(db, "pro")
        tenant.subscription_status = "active"
    elif status in PAYMENT_PROBLEM_STATUSES:
        tenant.subscription_status = status  # stays on Pro, but /generate returns 402
    elif status in ENDED_STATUSES:
        tenant.plan = _plan(db, "free")  # downgrade, don't lock them out
        tenant.subscription_status = "active"
    # other statuses (e.g. 'incomplete') change nothing on the tenant


def _plan(db: Session, name: str):
    plan = webhook_repo.get_plan_by_name(db, name)
    if plan is None:
        raise RuntimeError(f"Plan '{name}' is missing. Did you run the seed script?")
    return plan


def _period_end(sub: dict) -> datetime | None:
    # Newer Stripe API versions put this on the subscription item instead.
    ts = sub.get("current_period_end")
    if ts is None:
        items = (sub.get("items") or {}).get("data") or []
        ts = items[0].get("current_period_end") if items else None
    return datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None