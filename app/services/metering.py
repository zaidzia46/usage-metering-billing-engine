from dataclasses import dataclass
from datetime import datetime, timezone
from typing import NoReturn

from sqlalchemy.orm import Session

from app.models import UsageEvent
from app.repo import usage_repo
from app.services.pricing import TokenUsage, calculate_cost_micros, total_tokens

ALLOWED_STATUSES = {"active", "trialing"}


class TenantNotFound(Exception):
    pass


class IdempotencyKeyReused(Exception):
    """The same key was sent again with a different request body."""


class UpgradeRequired(Exception):
    """Maps to HTTP 402."""


class QuotaExceeded(Exception):
    """Maps to HTTP 429."""

    def __init__(self, message: str, retry_after_seconds: int):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class MeterResult:
    event: UsageEvent
    replayed: bool  # True if this was a retry of an earlier request


def month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def next_month_start(now: datetime) -> datetime:
    start = month_start(now)
    if start.month == 12:
        return start.replace(year=start.year + 1, month=1)
    return start.replace(month=start.month + 1)


def record_usage(
    db: Session,
    tenant_id: int,
    idempotency_key: str,
    usage: TokenUsage,
    api_calls: int = 1,
) -> MeterResult:
    try:
        return _record(db, tenant_id, idempotency_key, usage, api_calls)
    except Exception:
        db.rollback()  # releases the tenant lock immediately
        raise


def _record(db, tenant_id, idempotency_key, usage, api_calls) -> MeterResult:
    tenant = usage_repo.get_tenant_for_update(db, tenant_id)
    if tenant is None:
        raise TenantNotFound(f"Tenant {tenant_id} does not exist.")

    # Retry check comes BEFORE the quota check, so a retry always gets its
    # original answer, even if the tenant has since reached the limit.
    existing = usage_repo.find_event(db, tenant_id, idempotency_key)
    if existing is not None:
        if not _same_request(existing, usage, api_calls):
            raise IdempotencyKeyReused(
                "This Idempotency-Key was already used with a different request."
            )
        return MeterResult(event=existing, replayed=True)

    if tenant.subscription_status not in ALLOWED_STATUSES:
        raise UpgradeRequired(
            f"Subscription status is '{tenant.subscription_status}'. "
            "Please update your payment details."
        )

    now = datetime.now(timezone.utc)
    used_calls, used_tokens, _ = usage_repo.month_totals(
        db, tenant_id, month_start(now)
    )
    plan = tenant.plan
    requested_tokens = total_tokens(usage)

    if used_calls + api_calls > plan.api_call_limit:
        _reject(plan.name, "API calls", used_calls, api_calls, plan.api_call_limit, now)
    if used_tokens + requested_tokens > plan.token_limit:
        _reject(plan.name, "AI tokens", used_tokens, requested_tokens, plan.token_limit, now)

    event = UsageEvent(
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        api_calls=api_calls,
        input_tokens=usage.input_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        output_tokens=usage.output_tokens,
        reasoning_tokens=usage.reasoning_tokens,
        total_tokens=requested_tokens,
        cost_micros=calculate_cost_micros(usage, api_calls),
    )
    usage_repo.add_event(db, event)
    db.commit()  # saves the event AND releases the lock
    return MeterResult(event=event, replayed=False)


def _same_request(event: UsageEvent, usage: TokenUsage, api_calls: int) -> bool:
    return (
        event.api_calls == api_calls
        and event.input_tokens == usage.input_tokens
        and event.cached_input_tokens == usage.cached_input_tokens
        and event.output_tokens == usage.output_tokens
        and event.reasoning_tokens == usage.reasoning_tokens
    )


def _reject(plan_name, label, used, requested, limit, now) -> NoReturn:
    message = (
        f"Monthly {label} quota exceeded: used {used} + requested {requested} "
        f"exceeds the {plan_name} plan limit of {limit}."
    )
    if plan_name == "free":
        raise UpgradeRequired(message + " Upgrade to Pro for higher limits.")
    retry_after = int((next_month_start(now) - now).total_seconds())
    raise QuotaExceeded(message, retry_after_seconds=retry_after)