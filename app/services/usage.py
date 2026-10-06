from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.repo import usage_repo
from app.services.metering import TenantNotFound, month_start, next_month_start


@dataclass(frozen=True)
class QuotaLine:
    used: int
    limit: int
    remaining: int


@dataclass(frozen=True)
class UsageSummary:
    tenant_id: int
    plan: str
    subscription_status: str
    period_start: datetime
    period_end: datetime
    api_calls: QuotaLine
    tokens: QuotaLine
    cost_micros: int


def _line(used: int, limit: int) -> QuotaLine:
    return QuotaLine(used=used, limit=limit, remaining=max(limit - used, 0))


def get_usage_summary(db: Session, tenant_id: int) -> UsageSummary:
    tenant = usage_repo.get_tenant(db, tenant_id)
    if tenant is None:
        raise TenantNotFound(f"Tenant {tenant_id} does not exist.")

    now = datetime.now(timezone.utc)
    start = month_start(now)
    calls, tokens, cost = usage_repo.month_totals(db, tenant_id, start)
    plan = tenant.plan

    return UsageSummary(
        tenant_id=tenant.id,
        plan=plan.name,
        subscription_status=tenant.subscription_status,
        period_start=start,
        period_end=next_month_start(now),
        api_calls=_line(calls, plan.api_call_limit),
        tokens=_line(tokens, plan.token_limit),
        cost_micros=cost,
    )