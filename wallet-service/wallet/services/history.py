import uuid

from sqlalchemy import select

from wallet.db import Database
from wallet.errors import AppError
from wallet.models import LedgerEntry

MAX_BIGINT = 2**63 - 1


def parse_cursor(cursor: str | None) -> int | None:
    if cursor is None:
        return None
    if not cursor.isascii() or not cursor.isdigit() or int(cursor) > MAX_BIGINT:
        raise AppError(422, "invalid_cursor", "cursor must be a value returned as next_cursor")
    return int(cursor)


async def list_entries(
    db: Database, *, account_id: uuid.UUID, limit: int, cursor: str | None
) -> tuple[list[LedgerEntry], str | None]:
    """Return ledger entries newest first with keyset pagination by id."""
    before_id = parse_cursor(cursor)
    stmt = (
        select(LedgerEntry)
        .where(LedgerEntry.account_id == account_id)
        .order_by(LedgerEntry.id.desc())
        .limit(limit + 1)
    )
    if before_id is not None:
        stmt = stmt.where(LedgerEntry.id < before_id)
    async with db.transaction() as session:
        entries = list(await session.scalars(stmt))
    has_more = len(entries) > limit
    entries = entries[:limit]
    return entries, (str(entries[-1].id) if has_more else None)
