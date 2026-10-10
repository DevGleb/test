"""Rate limit hits (sliding-window log)."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_hits",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column(
            "hit_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_rate_limit_hits_account_id_hit_at",
        "rate_limit_hits",
        ["account_id", "hit_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_rate_limit_hits_account_id_hit_at", table_name="rate_limit_hits")
    op.drop_table("rate_limit_hits")
