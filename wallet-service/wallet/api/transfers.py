import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from wallet.db import Database
from wallet.deps import current_account_id, get_db
from wallet.money import format_money
from wallet.schemas import TransferCreate, TransferOut
from wallet.services.idempotency import (
    StoredResponse,
    request_fingerprint,
    required_idempotency_key,
    run_idempotent,
)
from wallet.services.transfers import execute_transfer

router = APIRouter(prefix="/v1/transfers", tags=["transfers"])


@router.post("", status_code=201, response_model=TransferOut)
async def create_transfer(
    body: TransferCreate,
    account_id: uuid.UUID = Depends(current_account_id),
    idempotency_key: str = Depends(required_idempotency_key),
    db: Database = Depends(get_db),
) -> JSONResponse:
    async def operation(session: AsyncSession) -> StoredResponse:
        transfer = await execute_transfer(
            session, from_id=account_id, to_id=body.to_account_id, amount=body.amount
        )
        return StoredResponse(201, TransferOut.of(transfer).model_dump(mode="json"))

    response, replayed = await run_idempotent(
        db,
        account_id=account_id,
        key=idempotency_key,
        request_hash=request_fingerprint(
            "POST /v1/transfers",
            {"to_account_id": str(body.to_account_id), "amount": format_money(body.amount)},
        ),
        operation=operation,
    )
    return JSONResponse(
        response.body,
        status_code=response.status_code,
        headers={"Idempotent-Replayed": "true"} if replayed else None,
    )
