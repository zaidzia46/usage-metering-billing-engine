# All prices are integers in micro-dollars (1 USD = 1_000_000 micros).
# Token prices are "micros per 1,000,000 tokens", so $1.00 per million tokens
# is written as 1_000_000.

INPUT_PRICE_PER_MTOK = 1_000_000          # $1.00 per 1M fresh input tokens
CACHED_INPUT_PRICE_PER_MTOK = 250_000     # $0.25 per 1M cached input tokens
OUTPUT_PRICE_PER_MTOK = 4_000_000         # $4.00 per 1M output tokens
# Reasoning tokens are billed at the output price, so they have no price of their own.

API_CALL_PRICE_MICROS = 2_000             # $0.002 per API call