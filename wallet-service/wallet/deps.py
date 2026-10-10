import uuid

from fastapi import Depends, Header, Request
from sqlalchemy import select

from wallet.config import Settings
from wallet.db import Database
from wallet.errors import unauthorized
from wallet.models import Account
from wallet.provider_client import ProviderClient
from wallet.security import hash_api_key
from wallet.services.rate_limit import check_rate_limit


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_provider(request: Request) -> ProviderClient:
    return request.app.state.provider


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


async def rate_limited_account_id(
    account_id: uuid.UUID = Depends(current_account_id),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> uuid.UUID:
    """Authenticate the caller and apply the per-key POST rate limit."""
    await check_rate_limit(db, account_id, settings.rate_limit_per_minute)
    return account_id
