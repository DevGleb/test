import uuid

import pytest

from tests.helpers import assert_error

URL = "/v1/transactions"


async def transfer(client, sender, recipient, amount: str) -> None:
    response = await client.post(
        "/v1/transfers",
        json={"to_account_id": str(recipient.id), "amount": amount},
        headers={**sender.headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert response.status_code == 201, response.text


async def test_history_shows_all_operation_types(client, make_account):
    alice = await make_account("alice", balance="20.00")
    bob = await make_account("bob", balance="5.00")
    await transfer(client, alice, bob, "3.00")
    await transfer(client, bob, alice, "1.00")
    await client.post("/v1/purchases", json={"credits": 10}, headers=alice.headers)

    response = await client.get(URL, headers=alice.headers)

    assert response.status_code == 200
    items = response.json()["items"]
    assert [(i["type"], i["amount_usd"], i["credits_delta"]) for i in items] == [
        ("purchase", "-1.00", 10),
        ("transfer_in", "1.00", 0),
        ("transfer_out", "-3.00", 0),
    ]
    assert [i["balance_usd_after"] for i in items] == ["17.00", "18.00", "17.00"]
    assert items[2]["counterparty_account_id"] == str(bob.id)
    assert response.json()["next_cursor"] is None


async def test_history_is_private(client, make_account):
    alice = await make_account("alice", balance="10.00")
    bob = await make_account("bob")
    carol = await make_account("carol")
    await transfer(client, alice, bob, "1.00")

    items = (await client.get(URL, headers=carol.headers)).json()["items"]
    assert items == []


async def test_cursor_pagination_walks_every_entry_once(client, make_account):
    alice = await make_account("alice", balance="10.00")
    bob = await make_account("bob")
    for _ in range(7):
        await transfer(client, alice, bob, "1.00")

    seen, cursor, pages = [], None, 0
    while True:
        params = {"limit": 3} | ({"cursor": cursor} if cursor else {})
        page = (await client.get(URL, params=params, headers=alice.headers)).json()
        seen += [item["id"] for item in page["items"]]
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert pages == 3
    assert len(seen) == len(set(seen)) == 7
    assert seen == sorted(seen, key=int, reverse=True)


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"limit": "x"}])
async def test_invalid_limit_is_422(client, make_account, params):
    alice = await make_account()
    response = await client.get(URL, params=params, headers=alice.headers)
    assert_error(response, 422, "validation_error")


async def test_invalid_cursor_is_422(client, make_account):
    alice = await make_account()
    response = await client.get(URL, params={"cursor": "abc"}, headers=alice.headers)
    assert_error(response, 422, "invalid_cursor")


async def test_history_requires_auth(client):
    assert_error(await client.get(URL), 401, "unauthorized")


async def test_cursor_beyond_bigint_is_422(client, make_account):
    alice = await make_account()
    response = await client.get(
        URL, params={"cursor": "99999999999999999999"}, headers=alice.headers
    )
    assert_error(response, 422, "invalid_cursor")
