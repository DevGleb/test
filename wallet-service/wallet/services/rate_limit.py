"""Sliding-window rate limit for POST requests, stored in Postgres."""

import math
import uuid
from datetime import timedelta

from sqlalchemy import delete, func, select

from wallet.db import Database
from wallet.errors import AppError
from wallet.models import RateLimitHit

WINDOW = timedelta(seconds=60)


async def check_rate_limit(db: Database, account_id: uuid.UUID, limit: int) -> None:
    retry_after: int | None = None
    async with db.transaction() as session:
        lock_key = func.hashtextextended(str(account_id), 0)
        await session.execute(select(func.pg_advisory_xact_lock(lock_key)))
        await session.execute(
            delete(RateLimitHit).where(
                RateLimitHit.account_id == account_id,
                RateLimitHit.hit_at <= func.now() - WINDOW,
            )
        )
        count, oldest, now = (
            await session.execute(
                select(func.count(), func.min(RateLimitHit.hit_at), func.now()).where(
                    RateLimitHit.account_id == account_id
                )
            )
        ).one()
        if count >= limit:
            retry_after = max(1, math.ceil((oldest + WINDOW - now).total_seconds()))
        else:
            session.add(RateLimitHit(account_id=account_id))

    if retry_after is not None:
        raise AppError(
            429,
            "rate_limited",
            f"Too many requests: at most {limit} POST requests per minute",
            {"limit": limit, "window_seconds": int(WINDOW.total_seconds())},
            headers={"Retry-After": str(retry_after)},
        )
