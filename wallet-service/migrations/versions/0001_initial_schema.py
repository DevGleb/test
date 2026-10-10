"""Initial schema."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("api_key_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "balance_usd", sa.Numeric(precision=18, scale=2), server_default="0", nullable=False
        ),
        sa.Column("credits", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("balance_usd >= 0", name="balance_usd_non_negative"),
        sa.CheckConstraint("credits >= 0", name="credits_non_negative"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("api_key_hash"),
    )
    op.create_table(
        "webhook_events",
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("invoice_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(
        op.f("ix_webhook_events_invoice_id"), "webhook_events", ["invoice_id"], unique=False
    )
    op.create_table(
        "deposits",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("invoice_id", sa.String(length=64), nullable=True),
        sa.Column("pay_url", sa.Text(), nullable=True),
        sa.Column("paid_amount", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("fee", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("credited_amount", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount > 0", name="deposit_amount_positive"),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("invoice_id"),
    )
    op.create_index(op.f("ix_deposits_account_id"), "deposits", ["account_id"], unique=False)
    op.create_index(
        "ix_deposits_status_created_at", "deposits", ["status", "created_at"], unique=False
    )
    op.create_table(
        "idempotency_keys",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
        ),
        sa.PrimaryKeyConstraint("account_id", "key"),
    )
    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("amount_usd", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("credits_delta", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("balance_usd_after", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("credits_after", sa.BigInteger(), nullable=False),
        sa.Column("reference_id", sa.Uuid(), nullable=False),
        sa.Column("counterparty_account_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ledger_entries_account_id_id", "ledger_entries", ["account_id", "id"], unique=False
    )
    op.create_table(
        "purchases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("credits", sa.BigInteger(), nullable=False),
        sa.Column("price", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("credits > 0", name="purchase_credits_positive"),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_purchases_account_id"), "purchases", ["account_id"], unique=False)
    op.create_table(
        "transfers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("from_account_id", sa.Uuid(), nullable=False),
        sa.Column("to_account_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount > 0", name="transfer_amount_positive"),
        sa.CheckConstraint("from_account_id <> to_account_id", name="transfer_not_to_self"),
        sa.ForeignKeyConstraint(
            ["from_account_id"],
            ["accounts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["to_account_id"],
            ["accounts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_transfers_from_account_id"), "transfers", ["from_account_id"], unique=False
    )
    op.create_index(
        op.f("ix_transfers_to_account_id"), "transfers", ["to_account_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_transfers_to_account_id"), table_name="transfers")
    op.drop_index(op.f("ix_transfers_from_account_id"), table_name="transfers")
    op.drop_table("transfers")
    op.drop_index(op.f("ix_purchases_account_id"), table_name="purchases")
    op.drop_table("purchases")
    op.drop_index("ix_ledger_entries_account_id_id", table_name="ledger_entries")
    op.drop_table("ledger_entries")
    op.drop_table("idempotency_keys")
    op.drop_index("ix_deposits_status_created_at", table_name="deposits")
    op.drop_index(op.f("ix_deposits_account_id"), table_name="deposits")
    op.drop_table("deposits")
    op.drop_index(op.f("ix_webhook_events_invoice_id"), table_name="webhook_events")
    op.drop_table("webhook_events")
    op.drop_table("accounts")
