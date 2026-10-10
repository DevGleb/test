"""Request and response bodies, with money as "0.00" strings."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from wallet.models import Account, Deposit, Transfer
from wallet.money import AmountStr, MoneyStr, format_money


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
    event_id: str = Field(min_length=1, max_length=128)
    invoice_id: str = Field(min_length=1, max_length=64)
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
