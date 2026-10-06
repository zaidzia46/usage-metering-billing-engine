from fastapi import APIRouter, Depends, Header, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import GenerateRequest, GenerateResponse
from app.services import metering
from app.services.pricing import TokenUsage

router = APIRouter()


def _error(status: int, code: str, message: str, headers: dict | None = None):
    return JSONResponse(
        status_code=status,
        content={"error": code, "message": message},
        headers=headers,
    )


@router.post("/generate", response_model=GenerateResponse)
def generate(
    body: GenerateRequest,
    response: Response,
    x_tenant_id: int = Header(),
    idempotency_key: str = Header(min_length=1, max_length=255),
    db: Session = Depends(get_db),
):
    usage = TokenUsage(
        input_tokens=body.input_tokens,
        cached_input_tokens=body.cached_input_tokens,
        output_tokens=body.output_tokens,
        reasoning_tokens=body.reasoning_tokens,
    )

    try:
        result = metering.record_usage(db, x_tenant_id, idempotency_key, usage)
    except metering.TenantNotFound as e:
        return _error(404, "tenant_not_found", str(e))
    except metering.IdempotencyKeyReused as e:
        return _error(409, "idempotency_key_reused", str(e))
    except metering.UpgradeRequired as e:
        return _error(402, "upgrade_required", str(e))
    except metering.QuotaExceeded as e:
        return _error(
            429,
            "quota_exceeded",
            str(e),
            headers={"Retry-After": str(e.retry_after_seconds)},
        )

    if result.replayed:
        response.headers["Idempotent-Replayed"] = "true"

    return GenerateResponse(
        event_id=result.event.id,
        idempotency_key=result.event.idempotency_key,
        api_calls=result.event.api_calls,
        total_tokens=result.event.total_tokens,
        cost_micros=result.event.cost_micros,
        created_at=result.event.created_at,
    )