from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_TOKENS_PER_REQUEST = 10_000_000  # sanity cap against typos and abuse


class GenerateRequest(BaseModel):
    input_tokens: int = Field(ge=0, le=MAX_TOKENS_PER_REQUEST)
    cached_input_tokens: int = Field(default=0, ge=0, le=MAX_TOKENS_PER_REQUEST)
    output_tokens: int = Field(ge=0, le=MAX_TOKENS_PER_REQUEST)
    reasoning_tokens: int = Field(default=0, ge=0, le=MAX_TOKENS_PER_REQUEST)

    @model_validator(mode="after")
    def cached_must_fit_in_input(self):
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached_input_tokens cannot exceed input_tokens")
        return self


class GenerateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_id: int
    idempotency_key: str
    api_calls: int
    total_tokens: int
    cost_micros: int
    created_at: datetime


class ErrorResponse(BaseModel):
    error: str
    message: str

class QuotaLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    used: int
    limit: int
    remaining: int


class UsageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tenant_id: int
    plan: str
    subscription_status: str
    period_start: datetime
    period_end: datetime
    api_calls: QuotaLineResponse
    tokens: QuotaLineResponse
    cost_micros: int