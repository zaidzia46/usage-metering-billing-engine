import pytest

from app.services.pricing import TokenUsage, calculate_cost_micros, total_tokens


# ---------- the worked example from the design discussion ----------
# input 10,000 (4,000 cached), output 2,000, reasoning 3,000, one API call
#   fresh input  6,000 x $1.00/M = 6,000 micros
#   cached input 4,000 x $0.25/M = 1,000 micros
#   output       5,000 x $4.00/M = 20,000 micros   (2,000 + 3,000 reasoning)
#   API call                      = 2,000 micros
#   total                         = 29,000 micros

EXAMPLE = TokenUsage(
    input_tokens=10_000,
    cached_input_tokens=4_000,
    output_tokens=2_000,
    reasoning_tokens=3_000,
)


def test_worked_example_total_cost():
    assert calculate_cost_micros(EXAMPLE, api_calls=1) == 29_000


def test_quota_tokens_do_not_double_count_cached():
    # 10,000 + 2,000 + 3,000. Adding all four columns would wrongly give 19,000.
    assert total_tokens(EXAMPLE) == 15_000


# ---------- the two pricing rules from the brief ----------

def test_cached_input_is_cheaper_than_fresh_input():
    fresh = calculate_cost_micros(TokenUsage(input_tokens=1_000), api_calls=0)
    cached = calculate_cost_micros(
        TokenUsage(input_tokens=1_000, cached_input_tokens=1_000), api_calls=0
    )
    assert fresh == 1_000   # 1,000 x $1.00/M
    assert cached == 250    # 1,000 x $0.25/M


def test_reasoning_tokens_are_billed_like_output_tokens():
    as_output = calculate_cost_micros(TokenUsage(output_tokens=5_000), api_calls=0)
    as_reasoning = calculate_cost_micros(TokenUsage(reasoning_tokens=5_000), api_calls=0)
    assert as_output == as_reasoning == 20_000  # 5,000 x $4.00/M


# ---------- money safety ----------

def test_cost_is_an_integer():
    assert isinstance(calculate_cost_micros(EXAMPLE), int)


def test_rounding_happens_once_at_the_end():
    # 2 cached tokens: 2 x 250,000 = 500,000 -> 0.5 micro -> rounds half up to 1
    assert calculate_cost_micros(TokenUsage(2, 2, 0, 0), api_calls=0) == 1
    # 1 cached token: 250,000 -> 0.25 micro -> rounds to 0
    assert calculate_cost_micros(TokenUsage(1, 1, 0, 0), api_calls=0) == 0


def test_zero_usage_costs_nothing():
    assert calculate_cost_micros(TokenUsage(), api_calls=0) == 0


# ---------- bad input is rejected ----------

def test_cached_cannot_exceed_input():
    with pytest.raises(ValueError):
        TokenUsage(input_tokens=100, cached_input_tokens=200)


def test_negative_tokens_rejected():
    with pytest.raises(ValueError):
        TokenUsage(output_tokens=-1)


def test_negative_api_calls_rejected():
    with pytest.raises(ValueError):
        calculate_cost_micros(TokenUsage(), api_calls=-1)


# ---------- end to end: /generate then /usage must agree ----------

def test_usage_endpoint_matches_pricing_rules(client, make_tenant):
    tenant = make_tenant()
    headers = {"X-Tenant-Id": str(tenant), "Idempotency-Key": "pricing-1"}
    body = {
        "input_tokens": 10_000,
        "cached_input_tokens": 4_000,
        "output_tokens": 2_000,
        "reasoning_tokens": 3_000,
    }

    generated = client.post("/generate", json=body, headers=headers)
    assert generated.status_code == 200
    assert generated.json()["cost_micros"] == 29_000
    assert generated.json()["total_tokens"] == 15_000

    usage = client.get("/usage", headers={"X-Tenant-Id": str(tenant)}).json()
    assert usage["cost_micros"] == 29_000
    assert usage["tokens"]["used"] == 15_000
    assert usage["api_calls"]["used"] == 1