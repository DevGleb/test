import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from wallet.errors import insufficient_funds
from wallet.models import Account, LedgerEntry, LedgerKind, Purchase
from wallet.money import credits_price
from wallet.services.accounts import lock_account


async def execute_purchase(
    session: AsyncSession, *, account_id: uuid.UUID, credits: int
) -> tuple[Purchase, Account]:
    price = credits_price(credits)
    account = await lock_account(session, account_id)
    if account.balance_usd < price:
        raise insufficient_funds(account.balance_usd, price)

    account.balance_usd -= price
    account.credits += credits
    purchase = Purchase(id=uuid.uuid4(), account_id=account_id, credits=credits, price=price)
    session.add(purchase)
    session.add(
        LedgerEntry(
            account_id=account_id,
            kind=LedgerKind.PURCHASE,
            amount_usd=-price,
            credits_delta=credits,
            balance_usd_after=account.balance_usd,
            credits_after=account.credits,
            reference_id=purchase.id,
        )
    )
    await session.flush()
    return purchase, account
