import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

Money = Numeric(18, 2)


class Base(DeclarativeBase):
    type_annotation_map = {
        Decimal: Money,
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
    }


class DepositStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    PARTIALLY_PAID = "partially_paid"
    EXPIRED = "expired"
    FAILED = "failed"


class LedgerKind(StrEnum):
    DEPOSIT = "deposit"
    TRANSFER_OUT = "transfer_out"
    TRANSFER_IN = "transfer_in"
    PURCHASE = "purchase"


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint("balance_usd >= 0", name="balance_usd_non_negative"),
        CheckConstraint("credits >= 0", name="credits_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100))
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    balance_usd: Mapped[Decimal] = mapped_column(default=Decimal("0.00"), server_default="0")
    credits: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Deposit(Base):
    __tablename__ = "deposits"
    __table_args__ = (
        CheckConstraint("amount > 0", name="deposit_amount_positive"),
        Index("ix_deposits_status_created_at", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), index=True)
    amount: Mapped[Decimal]
    status: Mapped[str] = mapped_column(
        String(20), default=DepositStatus.PENDING, server_default=DepositStatus.PENDING.value
    )
    invoice_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    pay_url: Mapped[str | None] = mapped_column(Text)
    paid_amount: Mapped[Decimal | None]
    fee: Mapped[Decimal | None]
    credited_amount: Mapped[Decimal | None]
    failure_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class WebhookEvent(Base):
    """Every accepted provider event, keyed by event_id for de-duplication."""

    __tablename__ = "webhook_events"

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    invoice_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20))
    outcome: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict[str, Any]]
    received_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Transfer(Base):
    __tablename__ = "transfers"
    __table_args__ = (
        CheckConstraint("amount > 0", name="transfer_amount_positive"),
        CheckConstraint("from_account_id <> to_account_id", name="transfer_not_to_self"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    from_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), index=True)
    to_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), index=True)
    amount: Mapped[Decimal]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Purchase(Base):
    __tablename__ = "purchases"
    __table_args__ = (CheckConstraint("credits > 0", name="purchase_credits_positive"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), index=True)
    credits: Mapped[int] = mapped_column(BigInteger)
    price: Mapped[Decimal]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class LedgerEntry(Base):
    """Append-only history of balance changes behind GET /v1/transactions."""

    __tablename__ = "ledger_entries"
    __table_args__ = (Index("ix_ledger_entries_account_id_id", "account_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"))
    kind: Mapped[str] = mapped_column(String(20))
    amount_usd: Mapped[Decimal]
    credits_delta: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    balance_usd_after: Mapped[Decimal]
    credits_after: Mapped[int] = mapped_column(BigInteger)
    reference_id: Mapped[uuid.UUID]
    counterparty_account_id: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any] | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
