"""Request and response bodies, with money as "0.00" strings."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from wallet.models import Account, Deposit, LedgerEntry, Purchase, Transfer
from wallet.money import MAX_CREDITS_PER_PURCHASE, AmountStr, MoneyStr, format_money


def _money_or_none(value: Decimal | None) -> str | None:
    return None if value is None else format_money(value)


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountOut(BaseModel):
    account_id: uuid.UUID
    name: str
    balance_usd: str
    credits: int
    created_at: datetime

    @classmethod
    def of(cls, account: Account) -> "AccountOut":
        return cls(
            account_id=account.id,
            name=account.name,
            balance_usd=format_money(account.balance_usd),
            credits=account.credits,
            created_at=account.created_at,
        )


class DepositCreate(RequestModel):
    amount: AmountStr


class DepositOut(BaseModel):
    deposit_id: uuid.UUID
    status: str
    amount: str
    invoice_id: str | None
    pay_url: str | None
    paid_amount: str | None
    fee: str | None
    credited_amount: str | None
    created_at: datetime

    @classmethod
    def of(cls, deposit: Deposit) -> "DepositOut":
        return cls(
            deposit_id=deposit.id,
            status=deposit.status,
            amount=format_money(deposit.amount),
            invoice_id=deposit.invoice_id,
            pay_url=deposit.pay_url,
            paid_amount=_money_or_none(deposit.paid_amount),
            fee=_money_or_none(deposit.fee),
            credited_amount=_money_or_none(deposit.credited_amount),
            created_at=deposit.created_at,
        )


class WebhookEventIn(BaseModel):
    event_id: str = Field(min_length=1, max_length=128, pattern=r"^[\x21-\x7e]+$")
    invoice_id: str = Field(min_length=1, max_length=64, pattern=r"^[\x21-\x7e]+$")
    status: Literal["paid", "partially_paid", "expired"]
    paid_amount: MoneyStr
    occurred_at: datetime


class TransferCreate(RequestModel):
    to_account_id: uuid.UUID
    amount: AmountStr


class TransferOut(BaseModel):
    transfer_id: uuid.UUID
    from_account_id: uuid.UUID
    to_account_id: uuid.UUID
    amount: str
    created_at: datetime

    @classmethod
    def of(cls, transfer: Transfer) -> "TransferOut":
        return cls(
            transfer_id=transfer.id,
            from_account_id=transfer.from_account_id,
            to_account_id=transfer.to_account_id,
            amount=format_money(transfer.amount),
            created_at=transfer.created_at,
        )


class PurchaseCreate(RequestModel):
    credits: StrictInt = Field(gt=0, le=MAX_CREDITS_PER_PURCHASE)


class PurchaseOut(BaseModel):
    purchase_id: uuid.UUID
    credits: int
    price_usd: str
    balance_usd: str
    credits_balance: int
    created_at: datetime

    @classmethod
    def of(cls, purchase: Purchase, account: Account) -> "PurchaseOut":
        return cls(
            purchase_id=purchase.id,
            credits=purchase.credits,
            price_usd=format_money(purchase.price),
            balance_usd=format_money(account.balance_usd),
            credits_balance=account.credits,
            created_at=purchase.created_at,
        )


class TransactionOut(BaseModel):
    id: str
    type: str
    amount_usd: str
    credits_delta: int
    balance_usd_after: str
    credits_after: int
    reference_id: uuid.UUID
    counterparty_account_id: uuid.UUID | None
    created_at: datetime

    @classmethod
    def of(cls, entry: LedgerEntry) -> "TransactionOut":
        return cls(
            id=str(entry.id),
            type=entry.kind,
            amount_usd=format_money(entry.amount_usd),
            credits_delta=entry.credits_delta,
            balance_usd_after=format_money(entry.balance_usd_after),
            credits_after=entry.credits_after,
            reference_id=entry.reference_id,
            counterparty_account_id=entry.counterparty_account_id,
            created_at=entry.created_at,
        )


class TransactionPage(BaseModel):
    items: list[TransactionOut]
    next_cursor: str | None
