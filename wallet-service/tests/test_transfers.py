import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from tests.helpers import assert_error, get_balance
from wallet.models import LedgerEntry, Transfer

URL = "/v1/transfers"


def headers(account, key: str | None = None) -> dict[str, str]:
    h = dict(account.headers)
    h["Idempotency-Key"] = key or uuid.uuid4().hex
    return h


async def count(db, model) -> int:
    async with db.transaction() as session:
        return await session.scalar(select(func.count()).select_from(model))


async def test_transfer_moves_money(client, db, make_account):
    alice = await make_account("alice", balance="10.00")
    bob = await make_account("bob")

    response = await client.post(
        URL, json={"to_account_id": str(bob.id), "amount": "3.50"}, headers=headers(alice)
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["from_account_id"] == str(alice.id)
    assert body["to_account_id"] == str(bob.id)
    assert body["amount"] == "3.50"
    assert await get_balance(db, alice.id) == Decimal("6.50")
    assert await get_balance(db, bob.id) == Decimal("3.50")
    assert await count(db, LedgerEntry) == 2


async def test_minimum_amount_is_one_cent(client, db, make_account):
    alice = await make_account("alice", balance="1.00")
    bob = await make_account("bob")
    ok = await client.post(
        URL, json={"to_account_id": str(bob.id), "amount": "0.01"}, headers=headers(alice)
    )
    assert ok.status_code == 201
    too_small = await client.post(
        URL, json={"to_account_id": str(bob.id), "amount": "0.00"}, headers=headers(alice)
    )
    assert_error(too_small, 422, "validation_error")


async def test_idempotency_key_is_required(client, make_account):
    alice = await make_account("alice", balance="10.00")
    bob = await make_account("bob")
    response = await client.post(
        URL, json={"to_account_id": str(bob.id), "amount": "1.00"}, headers=alice.headers
    )
    assert_error(response, 400, "idempotency_key_required")


async def test_same_key_same_body_replays_without_second_debit(client, db, make_account):
    alice = await make_account("alice", balance="10.00")
    bob = await make_account("bob")
    h = headers(alice, "key-1")

    first = await client.post(URL, json={"to_account_id": str(bob.id), "amount": "4.00"}, headers=h)
    second = await client.post(URL, json={"amount": "4", "to_account_id": str(bob.id)}, headers=h)

    assert first.status_code == second.status_code == 201
    assert second.json() == first.json()
    assert second.headers["Idempotent-Replayed"] == "true"
    assert "Idempotent-Replayed" not in first.headers
    assert await get_balance(db, alice.id) == Decimal("6.00")
    assert await count(db, Transfer) == 1


async def test_same_key_different_body_is_409(client, db, make_account):
    alice = await make_account("alice", balance="10.00")
    bob = await make_account("bob")
    h = headers(alice, "key-1")
    await client.post(URL, json={"to_account_id": str(bob.id), "amount": "4.00"}, headers=h)

    response = await client.post(
        URL, json={"to_account_id": str(bob.id), "amount": "5.00"}, headers=h
    )

    assert_error(response, 409, "idempotency_key_reused")
    assert await get_balance(db, alice.id) == Decimal("6.00")


async def test_keys_are_scoped_per_account(client, db, make_account):
    alice = await make_account("alice", balance="10.00")
    bob = await make_account("bob", balance="10.00")
    carol = await make_account("carol")
    body = {"to_account_id": str(carol.id), "amount": "1.00"}

    a = await client.post(URL, json=body, headers=headers(alice, "shared-key"))
    b = await client.post(URL, json=body, headers=headers(bob, "shared-key"))

    assert a.status_code == b.status_code == 201
    assert a.json()["transfer_id"] != b.json()["transfer_id"]
    assert await get_balance(db, carol.id) == Decimal("2.00")


async def test_insufficient_funds(client, db, make_account):
    alice = await make_account("alice", balance="5.00")
    bob = await make_account("bob")

    response = await client.post(
        URL, json={"to_account_id": str(bob.id), "amount": "5.01"}, headers=headers(alice)
    )

    error = assert_error(response, 409, "insufficient_funds")
    assert error["details"] == {"balance_usd": "5.00", "required_usd": "5.01"}
    assert await get_balance(db, alice.id) == Decimal("5.00")
    assert await get_balance(db, bob.id) == Decimal("0.00")
    assert await count(db, LedgerEntry) == 0


async def test_insufficient_funds_response_is_replayed_for_same_key(client, db, make_account):
    alice = await make_account("alice", balance="1.00")
    bob = await make_account("bob")
    h = headers(alice, "key-1")
    body = {"to_account_id": str(bob.id), "amount": "2.00"}

    first = await client.post(URL, json=body, headers=h)
    second = await client.post(URL, json=body, headers=h)

    assert first.status_code == second.status_code == 409
    assert second.json() == first.json()
    assert second.headers["Idempotent-Replayed"] == "true"


async def test_unknown_recipient_is_404(client, make_account):
    alice = await make_account("alice", balance="10.00")
    response = await client.post(
        URL, json={"to_account_id": str(uuid.uuid4()), "amount": "1.00"}, headers=headers(alice)
    )
    assert_error(response, 404, "account_not_found")


async def test_transfer_to_self_is_rejected(client, db, make_account):
    alice = await make_account("alice", balance="10.00")
    response = await client.post(
        URL, json={"to_account_id": str(alice.id), "amount": "1.00"}, headers=headers(alice)
    )
    assert_error(response, 422, "self_transfer")
    assert await get_balance(db, alice.id) == Decimal("10.00")


@pytest.mark.parametrize(
    "body",
    [
        {"amount": 1},
        {"amount": 1.5},
        {"amount": "1.001"},
        {"amount": "-1.00"},
        {"amount": "1.00", "to_account_id": "not-a-uuid"},
    ],
)
async def test_invalid_body_is_422(client, make_account, body):
    alice = await make_account("alice", balance="10.00")
    bob = await make_account("bob")
    response = await client.post(
        URL, json={"to_account_id": str(bob.id), **body}, headers=headers(alice)
    )
    assert_error(response, 422, "validation_error")


async def test_too_long_idempotency_key_is_rejected(client, make_account):
    alice = await make_account("alice", balance="10.00")
    bob = await make_account("bob")
    response = await client.post(
        URL,
        json={"to_account_id": str(bob.id), "amount": "1.00"},
        headers=headers(alice, "k" * 256),
    )
    assert_error(response, 422, "invalid_idempotency_key")


async def test_parallel_transfers_never_overdraw(client, db, make_account):
    alice = await make_account("alice", balance="10.00")
    bob = await make_account("bob")
    body = {"to_account_id": str(bob.id), "amount": "1.00"}

    responses = await asyncio.gather(
        *(client.post(URL, json=body, headers=headers(alice)) for _ in range(25))
    )

    codes = sorted(r.status_code for r in responses)
    assert codes == [201] * 10 + [409] * 15
    assert await get_balance(db, alice.id) == Decimal("0.00")
    assert await get_balance(db, bob.id) == Decimal("10.00")


async def test_parallel_requests_with_same_key_debit_once(client, db, make_account):
    alice = await make_account("alice", balance="10.00")
    bob = await make_account("bob")
    h = headers(alice, "same-key")
    body = {"to_account_id": str(bob.id), "amount": "3.00"}

    responses = await asyncio.gather(*(client.post(URL, json=body, headers=h) for _ in range(10)))

    assert {r.status_code for r in responses} == {201}
    assert len({r.json()["transfer_id"] for r in responses}) == 1
    assert await get_balance(db, alice.id) == Decimal("7.00")
    assert await count(db, Transfer) == 1


async def test_opposite_transfers_do_not_deadlock(client, db, make_account):
    alice = await make_account("alice", balance="50.00")
    bob = await make_account("bob", balance="50.00")
    a_to_b = {"to_account_id": str(bob.id), "amount": "1.00"}
    b_to_a = {"to_account_id": str(alice.id), "amount": "1.00"}

    requests = []
    for _ in range(15):
        requests.append(client.post(URL, json=a_to_b, headers=headers(alice)))
        requests.append(client.post(URL, json=b_to_a, headers=headers(bob)))
    responses = await asyncio.wait_for(asyncio.gather(*requests), timeout=30)

    assert {r.status_code for r in responses} == {201}
    assert await get_balance(db, alice.id) + await get_balance(db, bob.id) == Decimal("100.00")
