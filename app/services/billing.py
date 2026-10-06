import stripe
from sqlalchemy.orm import Session

from app.config import settings
from app.repo import tenant_repo

stripe.api_key = settings.stripe_secret_key
stripe.max_network_retries = 2


class TenantNotFound(Exception):
    pass


class AlreadySubscribed(Exception):
    """Maps to HTTP 409."""


class BillingProviderError(Exception):
    """Stripe failed or was unreachable. Maps to HTTP 502."""


def create_checkout_url(db: Session, tenant_id: int) -> str:
    try:
        customer_id = _ensure_customer(db, tenant_id)
        session = stripe.checkout.Session.create(
            mode="subscription",
            customer=customer_id,
            line_items=[{"price": settings.stripe_pro_price_id, "quantity": 1}],
            client_reference_id=str(tenant_id),
            metadata={"tenant_id": str(tenant_id)},
            subscription_data={"metadata": {"tenant_id": str(tenant_id)}},
            success_url=f"{settings.app_base_url}/checkout/success",
            cancel_url=f"{settings.app_base_url}/checkout/cancel",
        )
    except stripe.StripeError as e:
        db.rollback()
        raise BillingProviderError("Could not reach the payment provider.") from e
    except Exception:
        db.rollback()
        raise
    return session.url


def _ensure_customer(db: Session, tenant_id: int) -> str:
    tenant = tenant_repo.get_for_update(db, tenant_id)
    if tenant is None:
        raise TenantNotFound(f"Tenant {tenant_id} does not exist.")

    if tenant.plan.name == "pro" and tenant.subscription_status == "active":
        raise AlreadySubscribed("This tenant is already on the Pro plan.")

    if tenant.stripe_customer_id is None:
        customer = stripe.Customer.create(
            name=tenant.name,
            metadata={"tenant_id": str(tenant.id)},
            idempotency_key=f"tenant-customer-{tenant.id}",
        )
        tenant_repo.set_stripe_customer_id(db, tenant, customer.id)

    customer_id = tenant.stripe_customer_id
    db.commit()  # saves the customer link and releases the lock
    return customer_id