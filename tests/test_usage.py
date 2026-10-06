def test_usage_is_isolated_per_tenant(client, make_tenant):
    a = make_tenant("A")
    b = make_tenant("B")
    client.post(
        "/generate",
        json={"input_tokens": 100, "output_tokens": 50},
        headers={"X-Tenant-Id": str(a), "Idempotency-Key": "iso-1"},
    )

    usage_a = client.get("/usage", headers={"X-Tenant-Id": str(a)}).json()
    usage_b = client.get("/usage", headers={"X-Tenant-Id": str(b)}).json()

    assert usage_a["tokens"]["used"] == 150
    assert usage_a["api_calls"]["used"] == 1
    assert usage_b["tokens"]["used"] == 0
    assert usage_b["api_calls"]["used"] == 0
    assert usage_b["cost_micros"] == 0