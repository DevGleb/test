import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wallet.models import Account


async def lock_accounts(
    session: AsyncSession, ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, Account]:
    """Lock accounts FOR UPDATE in id order so opposite transfers cannot deadlock."""
    stmt = (
        select(Account)
        .where(Account.id.in_(set(ids)))
        .order_by(Account.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return {account.id: account for account in await session.scalars(stmt)}


async def lock_account(session: AsyncSession, account_id: uuid.UUID) -> Account:
    accounts = await lock_accounts(session, [account_id])
    return accounts[account_id]
