import uuid

from fastapi import APIRouter, Depends

from wallet.config import Settings
from wallet.db import Database
from wallet.deps import (
    current_account_id,
    get_db,
    get_provider,
    get_settings,
    rate_limited_account_id,
)
from wallet.provider_client import ProviderClient
from wallet.schemas import DepositCreate, DepositOut
from wallet.services import deposits

router = APIRouter(prefix="/v1/deposits", tags=["deposits"])


@router.post("", status_code=201)
async def create_deposit(
    body: DepositCreate,
    account_id: uuid.UUID = Depends(rate_limited_account_id),
    db: Database = Depends(get_db),
    provider: ProviderClient = Depends(get_provider),
    settings: Settings = Depends(get_settings),
) -> DepositOut:
    deposit = await deposits.create_deposit(
        db,
        provider,
        account_id=account_id,
        amount=body.amount,
        callback_url=settings.webhook_callback_url,
    )
    return DepositOut.of(deposit)


@router.get("/{deposit_id}")
async def get_deposit(
    deposit_id: uuid.UUID,
    account_id: uuid.UUID = Depends(current_account_id),
    db: Database = Depends(get_db),
) -> DepositOut:
    return DepositOut.of(
        await deposits.get_deposit(db, account_id=account_id, deposit_id=deposit_id)
    )
