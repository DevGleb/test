"""Deposits: creation through the provider and settlement from webhooks or reconciliation."""

import uuid
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from wallet.db import Database
from wallet.errors import AppError
from wallet.models import Deposit, DepositStatus, LedgerEntry, LedgerKind, WebhookEvent
from wallet.money import ZERO, deposit_split
from wallet.provider_client import ProviderClient, ProviderError, ProviderUnavailable
from wallet.services.accounts import lock_account

FinalStatus = Literal["paid", "partially_paid", "expired"]
FINAL_PROVIDER_STATUSES: frozenset[str] = frozenset({"paid", "partially_paid", "expired"})

WebhookOutcome = Literal["processed", "duplicate", "ignored"]


async def create_deposit(
    db: Database,
    provider: ProviderClient,
    *,
    account_id: uuid.UUID,
    amount: Decimal,
    callback_url: str,
) -> Deposit:
    deposit_id = uuid.uuid4()
    async with db.transaction() as session:
        session.add(Deposit(id=deposit_id, account_id=account_id, amount=amount))

    try:
        invoice = await provider.create_invoice(
            amount=amount, external_id=str(deposit_id), callback_url=callback_url
        )
    except ProviderError as exc:
        await _mark_failed(db, deposit_id, str(exc))
        details = {"deposit_id": str(deposit_id)}
        if isinstance(exc, ProviderUnavailable):
            raise AppError(
                503, "provider_unavailable", "Payment provider is unavailable, retry later", details
            ) from exc
        raise AppError(
            502, "provider_error", "Payment provider rejected the request", details
        ) from exc

    async with db.transaction() as session:
        deposit = await session.get_one(Deposit, deposit_id, with_for_update=True)
        deposit.invoice_id = invoice.invoice_id
        deposit.pay_url = invoice.pay_url
    return deposit


async def _mark_failed(db: Database, deposit_id: uuid.UUID, reason: str) -> None:
    async with db.transaction() as session:
        await session.execute(
            update(Deposit)
            .where(Deposit.id == deposit_id, Deposit.status == DepositStatus.PENDING)
            .values(status=DepositStatus.FAILED, failure_reason=reason[:500])
        )


async def get_deposit(db: Database, *, account_id: uuid.UUID, deposit_id: uuid.UUID) -> Deposit:
    async with db.transaction() as session:
        deposit = await session.get(Deposit, deposit_id)
    if deposit is None or deposit.account_id != account_id:
        raise AppError(404, "deposit_not_found", "Deposit not found")
    return deposit


async def settle_deposit(
    session: AsyncSession, deposit: Deposit, status: FinalStatus, paid_amount: Decimal
) -> None:
    """Move a locked pending deposit to its final state, crediting the account if paid."""
    if deposit.status != DepositStatus.PENDING:
        raise ValueError(f"deposit {deposit.id} is already {deposit.status}")
    if status == "expired":
        deposit.status = DepositStatus.EXPIRED
        deposit.paid_amount = deposit.fee = deposit.credited_amount = ZERO
        return

    fee, credited = deposit_split(paid_amount)
    deposit.status = DepositStatus(status)
    deposit.paid_amount = paid_amount
    deposit.fee = fee
    deposit.credited_amount = credited
    if credited <= 0:
        return

    account = await lock_account(session, deposit.account_id)
    account.balance_usd += credited
    session.add(
        LedgerEntry(
            account_id=account.id,
            kind=LedgerKind.DEPOSIT,
            amount_usd=credited,
            balance_usd_after=account.balance_usd,
            credits_after=account.credits,
            reference_id=deposit.id,
        )
    )


async def process_webhook(
    db: Database,
    *,
    event_id: str,
    invoice_id: str,
    status: FinalStatus,
    paid_amount: Decimal,
    payload: dict[str, Any],
) -> WebhookOutcome:
    async with db.transaction() as session:
        deposit = await session.scalar(
            select(Deposit).where(Deposit.invoice_id == invoice_id).with_for_update()
        )
        if deposit is None:
            raise AppError(
                404, "deposit_not_found", "No deposit for this invoice", {"invoice_id": invoice_id}
            )

        outcome: WebhookOutcome = (
            "processed" if deposit.status == DepositStatus.PENDING else "ignored"
        )
        inserted = await session.scalar(
            pg_insert(WebhookEvent)
            .values(
                event_id=event_id,
                invoice_id=invoice_id,
                status=status,
                outcome=outcome,
                payload=payload,
            )
            .on_conflict_do_nothing(index_elements=[WebhookEvent.event_id])
            .returning(WebhookEvent.event_id)
        )
        if inserted is None:
            return "duplicate"
        if outcome == "processed":
            await settle_deposit(session, deposit, status, paid_amount)
        return outcome
