import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from wallet.errors import AppError, insufficient_funds
from wallet.models import LedgerEntry, LedgerKind, Transfer
from wallet.services.accounts import lock_accounts


async def execute_transfer(
    session: AsyncSession, *, from_id: uuid.UUID, to_id: uuid.UUID, amount: Decimal
) -> Transfer:
    if from_id == to_id:
        raise AppError(422, "self_transfer", "Cannot transfer to the same account")

    accounts = await lock_accounts(session, [from_id, to_id])
    if to_id not in accounts:
        raise AppError(
            404, "account_not_found", "Recipient account not found", {"account_id": str(to_id)}
        )
    sender, recipient = accounts[from_id], accounts[to_id]
    if sender.balance_usd < amount:
        raise insufficient_funds(sender.balance_usd, amount)

    sender.balance_usd -= amount
    recipient.balance_usd += amount
    transfer = Transfer(
        id=uuid.uuid4(), from_account_id=from_id, to_account_id=to_id, amount=amount
    )
    session.add(transfer)
    session.add_all(
        [
            LedgerEntry(
                account_id=sender.id,
                kind=LedgerKind.TRANSFER_OUT,
                amount_usd=-amount,
                balance_usd_after=sender.balance_usd,
                credits_after=sender.credits,
                reference_id=transfer.id,
                counterparty_account_id=recipient.id,
            ),
            LedgerEntry(
                account_id=recipient.id,
                kind=LedgerKind.TRANSFER_IN,
                amount_usd=amount,
                balance_usd_after=recipient.balance_usd,
                credits_after=recipient.credits,
                reference_id=transfer.id,
                counterparty_account_id=sender.id,
            ),
        ]
    )
    await session.flush()
    return transfer
