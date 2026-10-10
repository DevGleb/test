import uuid

from fastapi import APIRouter, Depends

from wallet.db import Database
from wallet.deps import current_account_id, get_db
from wallet.models import Account
from wallet.schemas import AccountOut

router = APIRouter(prefix="/v1/accounts", tags=["accounts"])


@router.get("/me")
async def get_me(
    account_id: uuid.UUID = Depends(current_account_id),
    db: Database = Depends(get_db),
) -> AccountOut:
    async with db.transaction() as session:
        account = await session.get_one(Account, account_id)
    return AccountOut.of(account)
