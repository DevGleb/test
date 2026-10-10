import uuid

from fastapi import Depends, Header, Request
from sqlalchemy import select

from wallet.config import Settings
from wallet.db import Database
from wallet.errors import unauthorized
from wallet.models import Account
from wallet.security import hash_api_key


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Database:
    return request.app.state.db


async def current_account_id(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: Database = Depends(get_db),
) -> uuid.UUID:
    if not x_api_key:
        raise unauthorized()
    async with db.transaction() as session:
        account_id = await session.scalar(
            select(Account.id).where(Account.api_key_hash == hash_api_key(x_api_key))
        )
    if account_id is None:
        raise unauthorized()
    return account_id
