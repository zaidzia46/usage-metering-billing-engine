from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Plan, ProcessedWebhookEvent, Subscription


def claim_event(db: Session, event_id: str) -> bool:
    """Try to record this Stripe event as processed.

    Returns True if we are the first to see it, False if it is a replay.
    """
    stmt = (
        insert(ProcessedWebhookEvent)
        .values(stripe_event_id=event_id)
        .on_conflict_do_nothing(index_elements=["stripe_event_id"])
    )
    return db.execute(stmt).rowcount == 1


def get_plan_by_name(db: Session, name: str) -> Plan | None:
    return db.scalar(select(Plan).where(Plan.name == name))


def upsert_subscription(
    db: Session,
    tenant_id: int,
    stripe_subscription_id: str,
    status: str,
    current_period_end: datetime | None,
) -> Subscription:
    sub = db.scalar(select(Subscription).where(Subscription.tenant_id == tenant_id))
    if sub is None:
        sub = Subscription(tenant_id=tenant_id)
        db.add(sub)
    sub.stripe_subscription_id = stripe_subscription_id
    sub.status = status
    sub.current_period_end = current_period_end
    db.flush()
    return sub