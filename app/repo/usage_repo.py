from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Tenant, UsageEvent


def get_tenant_for_update(db: Session, tenant_id: int) -> Tenant | None:
    """Fetch the tenant and LOCK its row until the transaction ends."""
    stmt = select(Tenant).where(Tenant.id == tenant_id).with_for_update()
    return db.scalar(stmt)

def get_tenant(db: Session, tenant_id: int) -> Tenant | None:
    """Plain read, no lock. Used by read-only endpoints like /usage."""
    return db.get(Tenant, tenant_id)


def find_event(db: Session, tenant_id: int, idempotency_key: str) -> UsageEvent | None:
    stmt = select(UsageEvent).where(
        UsageEvent.tenant_id == tenant_id,
        UsageEvent.idempotency_key == idempotency_key,
    )
    return db.scalar(stmt)


def month_totals(db: Session, tenant_id: int, since: datetime) -> tuple[int, int, int]:
    """Return (api_calls, total_tokens, cost_micros) used since `since`."""
    stmt = select(
        func.coalesce(func.sum(UsageEvent.api_calls), 0),
        func.coalesce(func.sum(UsageEvent.total_tokens), 0),
        func.coalesce(func.sum(UsageEvent.cost_micros), 0),
    ).where(
        UsageEvent.tenant_id == tenant_id,
        UsageEvent.created_at >= since,
    )
    calls, tokens, cost = db.execute(stmt).one()
    return int(calls), int(tokens), int(cost)


def add_event(db: Session, event: UsageEvent) -> UsageEvent:
    db.add(event)
    db.flush()  # sends the INSERT now, so a duplicate key fails right here
    return event