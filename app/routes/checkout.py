from fastapi import APIRouter, Depends, Header
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import CheckoutResponse
from app.services import billing

router = APIRouter()


def _error(status: int, code: str, message: str):
    return JSONResponse(
        status_code=status, content={"error": code, "message": message}
    )


@router.post("/checkout", response_model=CheckoutResponse)
def checkout(
    x_tenant_id: int = Header(),
    db: Session = Depends(get_db),
):
    try:
        url = billing.create_checkout_url(db, x_tenant_id)
    except billing.TenantNotFound as e:
        return _error(404, "tenant_not_found", str(e))
    except billing.AlreadySubscribed as e:
        return _error(409, "already_subscribed", str(e))
    except billing.BillingProviderError as e:
        return _error(502, "billing_provider_error", str(e))
    return CheckoutResponse(checkout_url=url)


@router.get("/checkout/success", response_class=HTMLResponse)
def checkout_success():
    return "<h1>Payment received</h1><p>Your plan updates in a few seconds.</p>"


@router.get("/checkout/cancel", response_class=HTMLResponse)
def checkout_cancel():
    return "<h1>Checkout canceled</h1><p>No charge was made.</p>"