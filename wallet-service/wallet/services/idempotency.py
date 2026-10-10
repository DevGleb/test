"""Idempotency-Key handling: the key is claimed in the same transaction as the operation."""

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Header
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from wallet.db import Database
from wallet.errors import AppError
from wallet.models import IdempotencyKey

MAX_KEY_LENGTH = 255


@dataclass(frozen=True)
class StoredResponse:
    status_code: int
    body: dict[str, Any]


def request_fingerprint(operation: str, normalized_body: dict[str, Any]) -> str:
    """Hash the validated body so "5" and "5.00" count as the same request."""
    canonical = json.dumps(
        {"operation": operation, "body": normalized_body}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _check_key(key: str) -> str:
    if len(key) > MAX_KEY_LENGTH or not key.isascii() or not key.isprintable():
        raise AppError(
            422,
            "invalid_idempotency_key",
            f"Idempotency-Key must be printable ASCII, at most {MAX_KEY_LENGTH} characters",
        )
    return key


def required_idempotency_key(
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> str:
    if not idempotency_key or not idempotency_key.strip():
        raise AppError(400, "idempotency_key_required", "Idempotency-Key header is required")
    return _check_key(idempotency_key)


def optional_idempotency_key(
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> str | None:
    return None if idempotency_key is None else required_idempotency_key(idempotency_key)


async def _claim(
    session: AsyncSession, *, account_id: uuid.UUID, key: str, request_hash: str
) -> StoredResponse | None:
    inserted = await session.scalar(
        pg_insert(IdempotencyKey)
        .values(account_id=account_id, key=key, request_hash=request_hash)
        .on_conflict_do_nothing(index_elements=[IdempotencyKey.account_id, IdempotencyKey.key])
        .returning(IdempotencyKey.key)
    )
    if inserted is not None:
        return None

    existing = await session.scalar(
        select(IdempotencyKey).where(
            IdempotencyKey.account_id == account_id, IdempotencyKey.key == key
        )
    )
    if existing is None or existing.response_status is None or existing.response_body is None:
        raise AppError(409, "idempotency_key_in_use", "A request with this key is in progress")
    if existing.request_hash != request_hash:
        raise AppError(
            409,
            "idempotency_key_reused",
            "Idempotency-Key was already used with a different request body",
        )
    return StoredResponse(existing.response_status, existing.response_body)


async def run_idempotent(
    db: Database,
    *,
    account_id: uuid.UUID,
    key: str | None,
    request_hash: str,
    operation: Callable[[AsyncSession], Awaitable[StoredResponse]],
) -> tuple[StoredResponse, bool]:
    """Run the operation at most once per (account, key) and return (response, replayed)."""
    async with db.transaction() as session:
        if key is not None:
            stored = await _claim(
                session, account_id=account_id, key=key, request_hash=request_hash
            )
            if stored is not None:
                return stored, True

        try:
            async with session.begin_nested():
                response = await operation(session)
        except AppError as exc:
            if key is None:
                raise
            response = StoredResponse(exc.status_code, exc.body())

        if key is not None:
            await session.execute(
                update(IdempotencyKey)
                .where(IdempotencyKey.account_id == account_id, IdempotencyKey.key == key)
                .values(response_status=response.status_code, response_body=response.body)
            )
    return response, False
