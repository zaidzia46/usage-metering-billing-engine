import pytest

from app.jobs import reconcile
from app.models import Tenant

CUSTOMER = "cus_recon_1"


class FakeSub:
    def __init__(self, data):
        self._data = data

    def to_dict(self):
        return self._data


class FakeList:
    def __init__(self, subs):
        self.data = [FakeSub(s) for s in subs]


def sub(sub_id="sub_1", status="active", created=1000):
    return {
        "id": sub_id,
        "status": status,
        "created": created,
        "current_period_end": 1_800_000_000,
    }


def fake_stripe(monkeypatch, by_customer):
    """Replace Stripe's subscription lookup. A value that is an Exception gets raised."""

    def fake_list(customer, status, limit):
        answer = by_customer[customer]
        if isinstance(answer, Exception):
            raise answer
        return FakeList(answer)

    monkeypatch.setattr(reconcile.stripe.Subscription, "list", fake_list)
    monkeypatch.setattr(reconcile.time, "sleep", lambda seconds: None)  # no real waiting


def reload(db, tenant_id):
    db.expire_all()
    return db.get(Tenant, tenant_id)


def test_missed_webhook_is_repaired(db, make_tenant, monkeypatch):
    tenant = make_tenant(customer_id=CUSTOMER)  # we say free
    fake_stripe(monkeypatch, {CUSTOMER: [sub(status="active")]})  # Stripe says paid

    assert reconcile.reconcile_tenant(db, tenant, CUSTOMER) is True
    assert reload(db, tenant).plan.name == "pro"


def test_second_run_changes_nothing(db, make_tenant, monkeypatch):
    tenant = make_tenant(customer_id=CUSTOMER)
    fake_stripe(monkeypatch, {CUSTOMER: [sub(status="active")]})

    assert reconcile.reconcile_tenant(db, tenant, CUSTOMER) is True
    assert reconcile.reconcile_tenant(db, tenant, CUSTOMER) is False


def test_stripe_has_no_subscription_downgrades_pro(db, make_tenant, monkeypatch):
    tenant = make_tenant(plan="pro", customer_id=CUSTOMER)
    fake_stripe(monkeypatch, {CUSTOMER: []})

    assert reconcile.reconcile_tenant(db, tenant, CUSTOMER) is True
    assert reload(db, tenant).plan.name == "free"


def test_paid_subscription_beats_newer_canceled_one(db, make_tenant, monkeypatch):
    tenant = make_tenant(customer_id=CUSTOMER)
    subs = [
        sub("sub_new_canceled", status="canceled", created=2000),
        sub("sub_old_active", status="active", created=1000),
    ]
    fake_stripe(monkeypatch, {CUSTOMER: subs})

    reconcile.reconcile_tenant(db, tenant, CUSTOMER)
    assert reload(db, tenant).plan.name == "pro"


def test_failing_tenant_is_retried_then_reported(make_tenant, monkeypatch):
    make_tenant(customer_id=CUSTOMER)
    calls = []

    def failing_list(customer, status, limit):
        calls.append(customer)
        raise RuntimeError("stripe is down")

    monkeypatch.setattr(reconcile.stripe.Subscription, "list", failing_list)
    monkeypatch.setattr(reconcile.time, "sleep", lambda seconds: None)

    fixed, failed = reconcile.run_once()

    assert (fixed, failed) == (0, 1)
    assert len(calls) == reconcile.MAX_ATTEMPTS  # it really retried


def test_one_failing_tenant_does_not_stop_the_others(db, make_tenant, monkeypatch):
    bad = make_tenant("Bad", customer_id="cus_bad")
    good = make_tenant("Good", customer_id="cus_good")
    fake_stripe(
        monkeypatch,
        {"cus_bad": RuntimeError("boom"), "cus_good": [sub(status="active")]},
    )

    fixed, failed = reconcile.run_once()

    assert (fixed, failed) == (1, 1)
    assert reload(db, good).plan.name == "pro"
    assert reload(db, bad).plan.name == "free"


def test_main_exits_nonzero_when_something_failed(make_tenant, monkeypatch):
    make_tenant(customer_id=CUSTOMER)
    fake_stripe(monkeypatch, {CUSTOMER: RuntimeError("boom")})

    with pytest.raises(SystemExit) as exit_info:
        reconcile.main()

    assert exit_info.value.code == 1  # this is the "failure alert" a scheduler sees