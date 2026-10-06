import json
import logging

import stripe
from fastapi import APIRouter, Depends, Header, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.services import webhooks

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/webhooks/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    payload = await request.body()  # the RAW bytes, exactly as Stripe sent them

    if not stripe_signature:
        return JSONResponse(
            status_code=400,
            content={"error": "missing_signature", "message": "Missing Stripe-Signature header."},
        )

    try:
        stripe.Webhook.construct_event(
            payload, stripe_signature, settings.stripe_webhook_secret
        )
        event = json.loads(payload)
    except (stripe.SignatureVerificationError, ValueError):
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_signature", "message": "Webhook verification failed."},
        )

    try:
        outcome = await run_in_threadpool(webhooks.handle_event, db, event)
    except Exception:
        logger.exception("Webhook processing failed for event %s", event.get("id"))
        return JSONResponse(
            status_code=500,
            content={"error": "processing_failed", "message": "Will be retried."},
        )

    print(f"WEBHOOK {event.get('type')} {event.get('id')} -> {outcome}")
    return {"status": outcome}