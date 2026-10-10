"""Settle pending deposits whose webhook never arrived, using the provider's invoice state."""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Row, func, select, tuple_

from wallet.db import Database
from wallet.models import Deposit, DepositStatus
from wallet.provider_client import ProviderClient, ProviderError
from wallet.services.deposits import FINAL_PROVIDER_STATUSES, mark_failed, settle_deposit

logger = logging.getLogger(__name__)

MIN_AGE_FOR_MISSING_INVOICE = timedelta(minutes=15)


@dataclass
class ReconcileReport:
    checked: int = 0
    settled: int = 0
    still_pending: int = 0
    already_final: int = 0
    failed_without_invoice: int = 0
    provider_errors: int = 0


async def reconcile_pending_deposits(
    db: Database,
    provider: ProviderClient,
    *,
    older_than: timedelta,
    batch_size: int = 500,
) -> ReconcileReport:
    report = ReconcileReport()
    after: tuple[datetime, uuid.UUID] | None = None
    while True:
        rows = await _next_batch(db, older_than=older_than, after=after, limit=batch_size)
        for deposit_id, invoice_id, _, old_enough_to_fail in rows:
            await _reconcile_one(db, provider, report, deposit_id, invoice_id, old_enough_to_fail)
        if len(rows) < batch_size:
            break
        after = (rows[-1].created_at, rows[-1].id)
    return report


async def _next_batch(
    db: Database,
    *,
    older_than: timedelta,
    after: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> list[Row]:
    stmt = (
        select(
            Deposit.id,
            Deposit.invoice_id,
            Deposit.created_at,
            (Deposit.created_at < func.now() - MIN_AGE_FOR_MISSING_INVOICE).label("old_enough"),
        )
        .where(
            Deposit.status == DepositStatus.PENDING,
            Deposit.created_at < func.now() - older_than,
        )
        .order_by(Deposit.created_at, Deposit.id)
        .limit(limit)
    )
    if after is not None:
        stmt = stmt.where(tuple_(Deposit.created_at, Deposit.id) > tuple_(*after))
    async with db.transaction() as session:
        return list((await session.execute(stmt)).all())


async def _reconcile_one(
    db: Database,
    provider: ProviderClient,
    report: ReconcileReport,
    deposit_id: uuid.UUID,
    invoice_id: str | None,
    old_enough_to_fail: bool,
) -> None:
    report.checked += 1
    if invoice_id is None:
        if old_enough_to_fail:
            await mark_failed(db, deposit_id, "invoice creation never completed")
            report.failed_without_invoice += 1
        return

    try:
        invoice = await provider.get_invoice(invoice_id)
    except ProviderError as exc:
        logger.warning("Reconcile: cannot fetch invoice %s: %s", invoice_id, exc)
        report.provider_errors += 1
        return

    if invoice.status not in FINAL_PROVIDER_STATUSES:
        report.still_pending += 1
        return

    async with db.transaction() as session:
        deposit = await session.get_one(Deposit, deposit_id, with_for_update=True)
        if deposit.status != DepositStatus.PENDING:
            report.already_final += 1
            return
        await settle_deposit(session, deposit, invoice.status, invoice.paid_amount)
        report.settled += 1
        logger.info("Reconcile: deposit %s settled as %s", deposit_id, invoice.status)
