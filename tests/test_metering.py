import uuid

from app.models import UsageEvent


def post(client, tenant_id, key, body=None):
    body = body or {"input_tokens": 10, "output_tokens": 5}
    headers = {"X-Tenant-Id": str(tenant_id), "Idempotency-Key": key}
    return client.post("/generate", json=body, headers=headers)


def add_usage(db, tenant_id, api_calls=0, tokens=0):
    """Insert usage directly, to reach a limit without sending thousands of requests."""
    db.add(
        UsageEvent(
            tenant_id=tenant_id,
            idempotency_key=f"seed-{uuid.uuid4()}",
            api_calls=api_calls,
            input_tokens=tokens,
            total_tokens=tokens,
        )
    )
    db.commit()


# ---------- Probe 1: idempotency ----------

def test_same_key_twice_creates_one_event(client, db, make_tenant):
    tenant = make_tenant()
    first = post(client, tenant, "key-1")
    second = post(client, tenant, "key-1")

    assert first.status_code == 200
    assert second.status_code == 200
    assert "idempotent-replayed" not in first.headers
    assert second.headers["idempotent-replayed"] == "true"
    assert first.json() == second.json()  # the retry mirrors the original
    assert db.query(UsageEvent).count() == 1


def test_same_key_different_body_is_rejected(client, db, make_tenant):
    tenant = make_tenant()
    post(client, tenant, "key-1", {"input_tokens": 10, "output_tokens": 5})
    r = post(client, tenant, "key-1", {"input_tokens": 10, "output_tokens": 6})

    assert r.status_code == 409
    assert r.json()["error"] == "idempotency_key_reused"
    assert db.query(UsageEvent).count() == 1


def test_tenants_can_use_the_same_key(client, db, make_tenant):
    a = make_tenant("A")
    b = make_tenant("B")
    ra = post(client, a, "shared-key")
    rb = post(client, b, "shared-key")

    assert ra.status_code == rb.status_code == 200
    assert ra.json()["event_id"] != rb.json()["event_id"]
    assert db.query(UsageEvent).count() == 2


# ---------- Probe 2: quota boundary ----------

def test_token_boundary_exact_limit_allowed_then_blocked(client, make_tenant, db):
    tenant = make_tenant(plan="free")  # limit: 100,000 tokens
    add_usage(db, tenant, tokens=99_999)
    one_token = {"input_tokens": 1, "output_tokens": 0}

    at_limit = post(client, tenant, "k1", one_token)   # 100,000 / 100,000
    over = post(client, tenant, "k2", one_token)       # would be 100,001

    assert at_limit.status_code == 200
    assert over.status_code == 402
    assert over.json()["error"] == "upgrade_required"
    assert "AI tokens" in over.json()["message"]


def test_api_call_boundary_exact_limit_allowed_then_blocked(client, make_tenant, db):
    tenant = make_tenant(plan="free")  # limit: 1,000 calls
    add_usage(db, tenant, api_calls=999)

    at_limit = post(client, tenant, "k1")  # 1,000 / 1,000
    over = post(client, tenant, "k2")

    assert at_limit.status_code == 200
    assert over.status_code == 402
    assert "API calls" in over.json()["message"]


def test_blocked_request_records_nothing(client, make_tenant, db):
    tenant = make_tenant(plan="free")
    add_usage(db, tenant, tokens=100_000)

    r = post(client, tenant, "k1", {"input_tokens": 1, "output_tokens": 0})

    assert r.status_code == 402
    assert db.query(UsageEvent).count() == 1  # only the seeded row


def test_pro_plan_over_limit_returns_429_with_retry_after(client, make_tenant, db):
    tenant = make_tenant(plan="pro")  # limit: 5,000,000 tokens
    add_usage(db, tenant, tokens=5_000_000)

    r = post(client, tenant, "k1", {"input_tokens": 1, "output_tokens": 0})

    assert r.status_code == 429
    assert r.json()["error"] == "quota_exceeded"
    retry_after = int(r.headers["retry-after"])
    assert 0 < retry_after <= 31 * 24 * 3600


def test_retry_of_successful_request_at_limit_is_not_blocked(client, make_tenant, db):
    tenant = make_tenant(plan="free")
    add_usage(db, tenant, tokens=99_999)
    one_token = {"input_tokens": 1, "output_tokens": 0}

    first = post(client, tenant, "k1", one_token)   # lands exactly on the limit
    retry = post(client, tenant, "k1", one_token)   # tenant is now full

    assert first.status_code == 200
    assert retry.status_code == 200
    assert retry.headers["idempotent-replayed"] == "true"


def test_past_due_subscription_returns_402(client, make_tenant):
    tenant = make_tenant(plan="pro", status="past_due")
    r = post(client, tenant, "k1")

    assert r.status_code == 402
    assert r.json()["error"] == "upgrade_required"


# ---------- Validation: clean 4xx, never a 500 ----------

def test_cached_tokens_cannot_exceed_input(client, make_tenant):
    tenant = make_tenant()
    body = {"input_tokens": 100, "cached_input_tokens": 200, "output_tokens": 1}
    assert post(client, tenant, "k1", body).status_code == 422


def test_negative_tokens_rejected(client, make_tenant):
    tenant = make_tenant()
    assert post(client, tenant, "k1", {"input_tokens": -5, "output_tokens": 1}).status_code == 422


def test_missing_idempotency_key_rejected(client, make_tenant):
    tenant = make_tenant()
    r = client.post(
        "/generate",
        json={"input_tokens": 1, "output_tokens": 1},
        headers={"X-Tenant-Id": str(tenant)},
    )
    assert r.status_code == 422


def test_unknown_tenant_returns_404(client):
    r = post(client, 999, "k1")
    assert r.status_code == 404
    assert r.json()["error"] == "tenant_not_found"