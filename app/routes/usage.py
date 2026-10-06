from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import UsageResponse
from app.services import metering
from app.services.usage import get_usage_summary

router = APIRouter()


@router.get("/usage", response_model=UsageResponse)
def usage(
    x_tenant_id: int = Header(),
    db: Session = Depends(get_db),
):
    try:
        return get_usage_summary(db, x_tenant_id)
    except metering.TenantNotFound as e:
        return JSONResponse(
            status_code=404,
            content={"error": "tenant_not_found", "message": str(e)},
        )