from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError

from wallet.config import Settings
from wallet.db import Database
from wallet.deps import get_db, get_settings
from wallet.errors import validation_error
from wallet.schemas import WebhookEventIn
from wallet.security import verify_webhook_signature
from wallet.services import deposits

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])


@router.post("/provider")
async def provider_webhook(
    request: Request,
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    raw = await request.body()
    verify_webhook_signature(
        secret=settings.webhook_secret,
        timestamp=request.headers.get("X-Timestamp"),
        signature=request.headers.get("X-Signature"),
        body=raw,
        tolerance_seconds=settings.webhook_tolerance_seconds,
    )
    try:
        event = WebhookEventIn.model_validate_json(raw)
    except ValidationError as exc:
        raise validation_error(list(exc.errors())) from exc

    outcome = await deposits.process_webhook(
        db,
        event_id=event.event_id,
        invoice_id=event.invoice_id,
        status=event.status,
        paid_amount=event.paid_amount,
        payload=event.model_dump(mode="json"),
    )
    return {"status": outcome}
