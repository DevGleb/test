import asyncio
import uuid

import pytest
from sqlalchemy import text

from tests.helpers import assert_error, signed_webhook

LIMIT = 3


@pytest.fixture
def settings(settings):
    settings.rate_limit_per_minute = LIMIT
    return settings


async def purchase(client, account):
    return await client.post("/v1/purchases", json={"credits": 1}, headers=account.headers)


async def test_post_over_limit_gets_429_with_retry_after(client, make_account):
    alice = await make_account(balance="10.00")
    bob = await make_account("bob")

    responses = [
        await purchase(client, alice),
        await client.post(
            "/v1/transfers",
            json={"to_account_id": str(bob.id), "amount": "1.00"},
            headers={**alice.headers, "Idempotency-Key": uuid.uuid4().hex},
        ),
        await client.post("/v1/deposits", json={"amount": "1.00"}, headers=alice.headers),
    ]
    assert [r.status_code for r in responses] == [201, 201, 201]

    limited = await purchase(client, alice)

    error = assert_error(limited, 429, "rate_limited")
    assert error["details"] == {"limit": LIMIT, "window_seconds": 60}
    assert 1 <= int(limited.headers["Retry-After"]) <= 60


async def test_get_requests_are_not_limited(client, make_account):
    alice = await make_account(balance="10.00")
    for _ in range(LIMIT):
        await purchase(client, alice)
    for _ in range(5):
        assert (await client.get("/v1/accounts/me", headers=alice.headers)).status_code == 200


async def test_limit_is_per_api_key(client, make_account):
    alice = await make_account("alice", balance="10.00")
    bob = await make_account("bob", balance="10.00")
    for _ in range(LIMIT):
        await purchase(client, alice)
    assert (await purchase(client, alice)).status_code == 429
    assert (await purchase(client, bob)).status_code == 201


async def test_webhook_is_not_limited(client, make_account):
    alice = await make_account()
    for _ in range(LIMIT):
        await client.post("/v1/deposits", json={"amount": "1.00"}, headers=alice.headers)

    for i in range(LIMIT + 2):
        body, headers = signed_webhook(
            {
                "event_id": f"evt_{i}",
                "invoice_id": "inv_x",
                "status": "paid",
                "paid_amount": "1.00",
                "occurred_at": "2026-01-01T00:00:00+00:00",
            }
        )
        response = await client.post("/v1/webhooks/provider", content=body, headers=headers)
        assert response.status_code == 404


async def test_window_slides(client, db, make_account):
    alice = await make_account(balance="10.00")
    for _ in range(LIMIT):
        await purchase(client, alice)
    assert (await purchase(client, alice)).status_code == 429

    async with db.transaction() as session:
        await session.execute(
            text(
                "UPDATE rate_limit_hits SET hit_at = now() - interval '61 seconds' "
                "WHERE id = (SELECT min(id) FROM rate_limit_hits)"
            )
        )

    assert (await purchase(client, alice)).status_code == 201
    assert (await purchase(client, alice)).status_code == 429


async def test_parallel_requests_cannot_exceed_limit(client, make_account):
    alice = await make_account(balance="10.00")
    responses = await asyncio.gather(*(purchase(client, alice) for _ in range(10)))
    assert sorted(r.status_code for r in responses) == [201] * LIMIT + [429] * (10 - LIMIT)
