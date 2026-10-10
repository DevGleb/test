import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from wallet.db import Database
from wallet.deps import current_account_id, get_db
from wallet.schemas import PurchaseCreate, PurchaseOut
from wallet.services.idempotency import (
    StoredResponse,
    optional_idempotency_key,
    request_fingerprint,
    run_idempotent,
)
from wallet.services.purchases import execute_purchase

router = APIRouter(prefix="/v1/purchases", tags=["purchases"])


@router.post("", status_code=201, response_model=PurchaseOut)
async def create_purchase(
    body: PurchaseCreate,
    account_id: uuid.UUID = Depends(current_account_id),
    idempotency_key: str | None = Depends(optional_idempotency_key),
    db: Database = Depends(get_db),
) -> JSONResponse:
    async def operation(session: AsyncSession) -> StoredResponse:
        purchase, account = await execute_purchase(
            session, account_id=account_id, credits=body.credits
        )
        return StoredResponse(201, PurchaseOut.of(purchase, account).model_dump(mode="json"))

    response, replayed = await run_idempotent(
        db,
        account_id=account_id,
        key=idempotency_key,
        request_hash=request_fingerprint("POST /v1/purchases", {"credits": body.credits}),
        operation=operation,
    )
    return JSONResponse(
        response.body,
        status_code=response.status_code,
        headers={"Idempotent-Replayed": "true"} if replayed else None,
    )
