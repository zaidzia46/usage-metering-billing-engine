import hashlib
import hmac
import json
import time

from app.config import settings
from app.models import ProcessedWebhookEvent, Subscription, Tenant

CUSTOMER = "cus_test_1"


def signed_post(client, event, secret=None):
    """Send an event signed exactly the way Stripe signs it."""
    payload = json.dumps(event).encode()
    timestamp = int(time.time())
    secret = secret or settings.stripe_webhook_secret
    digest = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    return client.post(
        "/webhooks/stripe",
        content=payload,
        headers={
            "Stripe-Signature": f"t={timestamp},v1={digest}",
            "Content-Type": "application/json",
        },
    )


def checkout_event(event_id="evt_1", customer=CUSTOMER, payment_status="paid"):
    return {
        "id": event_id,
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "mode": "subscription",
                "customer": customer,
                "subscription": "sub_test_1",
                "payment_status": payment_status,
            }
        },
    }


def subscription_event(event_type, status, event_id="evt_2", customer=CUSTOMER):
    return {
        "id": event_id,
        "type": event_type,
        "data": {
            "object": {
                "id": "sub_test_1",
                "customer": customer,
                "status": status,
                "current_period_end": 1_800_000_000,
            }
        },
    }


def reload(db, tenant_id):
    db.expire_all()  # forget cached rows, so we see what the route committed
    return db.get(Tenant, tenant_id)


# ---------- Probe 4: forged events ----------

def test_forged_signature_returns_400_and_changes_nothing(client, db, make_tenant):
    tenant = make_tenant(customer_id=CUSTOMER)
    r = client.post(
        "/webhooks/stripe",
        content=json.dumps(checkout_event()).encode(),
        headers={"Stripe-Signature": "t=1,v1=fake"},
    )

    assert r.status_code == 400
    assert r.json()["error"] == "invalid_signature"
    assert reload(db, tenant).plan.name == "free"
    assert db.query(ProcessedWebhookEvent).count() == 0


def test_missing_signature_returns_400(client, make_tenant):
    make_tenant(customer_id=CUSTOMER)
    r = client.post("/webhooks/stripe", content=json.dumps(checkout_event()).encode())
    assert r.status_code == 400
    assert r.json()["error"] == "missing_signature"


def test_wrong_secret_returns_400(client, db, make_tenant):
    tenant = make_tenant(customer_id=CUSTOMER)
    r = signed_post(client, checkout_event(), secret="whsec_not_the_real_one")

    assert r.status_code == 400
    assert reload(db, tenant).plan.name == "free"


def test_tampered_body_is_rejected(client, db, make_tenant):
    """A valid signature for one body must not work for a different body."""
    tenant = make_tenant(customer_id=CUSTOMER)
    original = json.dumps(checkout_event("evt_a")).encode()
    timestamp = int(time.time())
    digest = hmac.new(
        settings.stripe_webhook_secret.encode(),
        f"{timestamp}.".encode() + original,
        hashlib.sha256,
    ).hexdigest()
    tampered = json.dumps(checkout_event("evt_b")).encode()

    r = client.post(
        "/webhooks/stripe",
        content=tampered,
        headers={"Stripe-Signature": f"t={timestamp},v1={digest}"},
    )

    assert r.status_code == 400
    assert reload(db, tenant).plan.name == "free"


# ---------- Probe 3: a verified event upgrades the tenant ----------

def test_checkout_completed_upgrades_free_to_pro(client, db, make_tenant):
    tenant = make_tenant(customer_id=CUSTOMER)
    r = signed_post(client, checkout_event())

    assert r.status_code == 200
    assert r.json() == {"status": "processed"}
    t = reload(db, tenant)
    assert t.plan.name == "pro"
    assert t.subscription_status == "active"
    assert db.query(Subscription).filter_by(tenant_id=tenant).one().status == "active"

    usage = client.get("/usage", headers={"X-Tenant-Id": str(tenant)}).json()
    assert usage["plan"] == "pro"
    assert usage["api_calls"]["limit"] == 50_000
    assert usage["tokens"]["limit"] == 5_000_000


def test_unpaid_checkout_does_not_upgrade(client, db, make_tenant):
    tenant = make_tenant(customer_id=CUSTOMER)
    r = signed_post(client, checkout_event(payment_status="unpaid"))

    assert r.status_code == 200
    assert reload(db, tenant).plan.name == "free"


# ---------- Probe 4: replayed events are processed once ----------

def test_replayed_event_is_processed_once(client, db, make_tenant):
    tenant = make_tenant(customer_id=CUSTOMER)
    event = checkout_event("evt_replay")

    first = signed_post(client, event)
    second = signed_post(client, event)

    assert first.json() == {"status": "processed"}
    assert second.status_code == 200  # Stripe must get a 2xx, or it keeps retrying
    assert second.json() == {"status": "duplicate"}
    assert db.query(ProcessedWebhookEvent).count() == 1
    assert db.query(Subscription).count() == 1
    assert reload(db, tenant).plan.name == "pro"


# ---------- later subscription changes ----------

def test_subscription_deleted_downgrades_to_free(client, db, make_tenant):
    tenant = make_tenant(plan="pro", customer_id=CUSTOMER)
    r = signed_post(client, subscription_event("customer.subscription.deleted", "canceled"))

    assert r.json() == {"status": "processed"}
    t = reload(db, tenant)
    assert t.plan.name == "free"
    assert t.subscription_status == "active"  # downgraded, not locked out


def test_past_due_keeps_pro_but_blocks_generate_with_402(client, db, make_tenant):
    tenant = make_tenant(plan="pro", customer_id=CUSTOMER)
    signed_post(client, subscription_event("customer.subscription.updated", "past_due"))

    t = reload(db, tenant)
    assert t.plan.name == "pro"
    assert t.subscription_status == "past_due"

    r = client.post(
        "/generate",
        json={"input_tokens": 1, "output_tokens": 1},
        headers={"X-Tenant-Id": str(tenant), "Idempotency-Key": "k1"},
    )
    assert r.status_code == 402


def test_recovered_payment_restores_access(client, db, make_tenant):
    tenant = make_tenant(plan="pro", status="past_due", customer_id=CUSTOMER)
    signed_post(client, subscription_event("customer.subscription.updated", "active"))

    assert reload(db, tenant).subscription_status == "active"


# ---------- unknown things are ignored, not errors ----------

def test_unknown_customer_is_ignored(client, db, make_tenant):
    tenant = make_tenant(customer_id=CUSTOMER)
    r = signed_post(client, checkout_event(customer="cus_stranger"))

    assert r.status_code == 200
    assert reload(db, tenant).plan.name == "free"


def test_unhandled_event_type_is_ignored(client):
    event = {"id": "evt_other", "type": "invoice.paid", "data": {"object": {}}}
    r = signed_post(client, event)

    assert r.status_code == 200
    assert r.json() == {"status": "ignored"}


# ---------- a crash must not mark the event as processed ----------

def test_failed_processing_returns_500_and_can_be_retried(
    client, db, make_tenant, monkeypatch
):
    from app.services import webhooks

    tenant = make_tenant(customer_id=CUSTOMER)
    real_handler = webhooks.HANDLERS["checkout.session.completed"]

    def boom(db, obj):
        raise RuntimeError("simulated crash")

    monkeypatch.setitem(webhooks.HANDLERS, "checkout.session.completed", boom)
    failed = signed_post(client, checkout_event("evt_retry"))

    assert failed.status_code == 500
    assert db.query(ProcessedWebhookEvent).count() == 0  # the claim was rolled back

    monkeypatch.setitem(webhooks.HANDLERS, "checkout.session.completed", real_handler)
    retried = signed_post(client, checkout_event("evt_retry"))

    assert retried.json() == {"status": "processed"}
    assert reload(db, tenant).plan.name == "pro"