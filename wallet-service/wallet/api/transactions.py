import uuid

from fastapi import APIRouter, Depends, Query

from wallet.db import Database
from wallet.deps import current_account_id, get_db
from wallet.schemas import TransactionOut, TransactionPage
from wallet.services.history import list_entries

router = APIRouter(prefix="/v1/transactions", tags=["transactions"])


@router.get("")
async def list_transactions(
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=20),
    account_id: uuid.UUID = Depends(current_account_id),
    db: Database = Depends(get_db),
) -> TransactionPage:
    entries, next_cursor = await list_entries(db, account_id=account_id, limit=limit, cursor=cursor)
    return TransactionPage(
        items=[TransactionOut.of(entry) for entry in entries], next_cursor=next_cursor
    )
