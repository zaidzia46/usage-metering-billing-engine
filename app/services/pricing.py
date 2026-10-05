from dataclasses import dataclass

from app.pricing_config import (
    API_CALL_PRICE_MICROS,
    CACHED_INPUT_PRICE_PER_MTOK,
    INPUT_PRICE_PER_MTOK,
    OUTPUT_PRICE_PER_MTOK,
)

MILLION = 1_000_000


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0          # total input, INCLUDING the cached part
    cached_input_tokens: int = 0   # subset of input_tokens
    output_tokens: int = 0         # excludes reasoning tokens
    reasoning_tokens: int = 0      # billed at the output price

    def __post_init__(self):
        values = (
            self.input_tokens,
            self.cached_input_tokens,
            self.output_tokens,
            self.reasoning_tokens,
        )
        if any(v < 0 for v in values):
            raise ValueError("token counts cannot be negative")
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached_input_tokens cannot exceed input_tokens")


def total_tokens(usage: TokenUsage) -> int:
    """Tokens counted against the quota. Cached tokens are already inside input_tokens."""
    return usage.input_tokens + usage.output_tokens + usage.reasoning_tokens


def calculate_cost_micros(usage: TokenUsage, api_calls: int = 1) -> int:
    """Cost in micro-dollars, as an integer. Rounds once, at the end."""
    if api_calls < 0:
        raise ValueError("api_calls cannot be negative")

    fresh_input = usage.input_tokens - usage.cached_input_tokens
    billable_output = usage.output_tokens + usage.reasoning_tokens

    exact = (
        fresh_input * INPUT_PRICE_PER_MTOK
        + usage.cached_input_tokens * CACHED_INPUT_PRICE_PER_MTOK
        + billable_output * OUTPUT_PRICE_PER_MTOK
    )
    token_cost = (exact + MILLION // 2) // MILLION  # divide once, round half up

    return token_cost + api_calls * API_CALL_PRICE_MICROS