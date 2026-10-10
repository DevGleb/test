import asyncio
import uuid
from decimal import Decimal

import pytest

from tests.helpers import assert_error, get_balance

URL = "/v1/purchases"


@pytest.mark.parametrize(
    ("credits", "price", "balance_after"),
    [(333, "33.30", "66.70"), (507, "48.17", "51.83"), (1005, "87.94", "12.06")],
)
async def test_purchase_debits_price_and_adds_credits(
    client, db, make_account, credits, price, balance_after
):
    alice = await make_account(balance="100.00")

    response = await client.post(URL, json={"credits": credits}, headers=alice.headers)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["price_usd"] == price
    assert body["balance_usd"] == balance_after
    assert body["credits_balance"] == credits
    me = (await client.get("/v1/accounts/me", headers=alice.headers)).json()
    assert (me["balance_usd"], me["credits"]) == (balance_after, credits)


async def test_purchase_with_exact_balance(client, db, make_account):
    alice = await make_account(balance="48.17")
    response = await client.post(URL, json={"credits": 507}, headers=alice.headers)
    assert response.status_code == 201
    assert await get_balance(db, alice.id) == Decimal("0.00")


async def test_purchase_insufficient_funds(client, db, make_account):
    alice = await make_account(balance="48.16")
    response = await client.post(URL, json={"credits": 507}, headers=alice.headers)
    error = assert_error(response, 409, "insufficient_funds")
    assert error["details"] == {"balance_usd": "48.16", "required_usd": "48.17"}
    assert await get_balance(db, alice.id) == Decimal("48.16")


@pytest.mark.parametrize("credits", ["507", 507.0, 507.5, True, None, 0, -1, 1_000_001, [507]])
async def test_credits_must_be_a_positive_integer(client, make_account, credits):
    alice = await make_account(balance="100.00")
    response = await client.post(URL, json={"credits": credits}, headers=alice.headers)
    assert_error(response, 422, "validation_error")


async def test_parallel_purchases_never_overdraw(client, db, make_account):
    alice = await make_account(balance="1.00")
    responses = await asyncio.gather(
        *(client.post(URL, json={"credits": 3}, headers=alice.headers) for _ in range(10))
    )
    assert sorted(r.status_code for r in responses) == [201] * 3 + [409] * 7
    assert await get_balance(db, alice.id) == Decimal("0.10")


async def test_optional_idempotency_key_is_honoured(client, db, make_account):
    alice = await make_account(balance="100.00")
    headers = {**alice.headers, "Idempotency-Key": uuid.uuid4().hex}

    first = await client.post(URL, json={"credits": 333}, headers=headers)
    second = await client.post(URL, json={"credits": 333}, headers=headers)
    conflict = await client.post(URL, json={"credits": 334}, headers=headers)

    assert second.json() == first.json()
    assert second.headers["Idempotent-Replayed"] == "true"
    assert_error(conflict, 409, "idempotency_key_reused")
    assert await get_balance(db, alice.id) == Decimal("66.70")
