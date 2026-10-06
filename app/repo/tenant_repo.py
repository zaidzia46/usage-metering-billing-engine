from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Tenant


def get_for_update(db: Session, tenant_id: int) -> Tenant | None:
    """Fetch the tenant and lock its row until the transaction ends."""
    stmt = select(Tenant).where(Tenant.id == tenant_id).with_for_update()
    return db.scalar(stmt)


def get_by_stripe_customer_id(db: Session, customer_id: str) -> Tenant | None:
    stmt = select(Tenant).where(Tenant.stripe_customer_id == customer_id)
    return db.scalar(stmt)


def set_stripe_customer_id(db: Session, tenant: Tenant, customer_id: str) -> None:
    tenant.stripe_customer_id = customer_id
    db.flush()