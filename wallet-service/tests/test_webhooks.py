import asyncio
import json
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from tests.helpers import assert_error, get_balance, sign, signed_webhook
from wallet.models import LedgerEntry

WEBHOOK_URL = "/v1/webhooks/provider"


async def pending_deposit(client, account, amount: str = "10.00") -> dict:
    response = await client.post("/v1/deposits", json={"amount": amount}, headers=account.headers)
    assert response.status_code == 201, response.text
    return response.json()


def make_event(invoice_id: str, status: str = "paid", paid_amount: str = "10.00", **extra) -> dict:
    return {
        "event_id": f"evt_{uuid.uuid4().hex}",
        "invoice_id": invoice_id,
        "status": status,
        "paid_amount": paid_amount,
        "occurred_at": datetime.now(UTC).isoformat(),
        **extra,
    }


async def send(client, event: dict, **sign_kwargs):
    body, headers = signed_webhook(event, **sign_kwargs)
    return await client.post(WEBHOOK_URL, content=body, headers=headers)


async def get_deposit(client, account, deposit_id: str) -> dict:
    return (await client.get(f"/v1/deposits/{deposit_id}", headers=account.headers)).json()


@pytest.mark.parametrize(
    ("status", "paid", "fee", "credited"),
    [
        ("paid", "10.00", "0.15", "9.85"),
        ("partially_paid", "7.50", "0.11", "7.39"),
        ("partially_paid", "0.99", "0.01", "0.98"),
    ],
)
async def test_paid_webhook_credits_net_amount(
    client, db, make_account, status, paid, fee, credited
):
    alice = await make_account()
    deposit = await pending_deposit(client, alice)

    response = await send(client, make_event(deposit["invoice_id"], status, paid))

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "processed"}
    assert await get_balance(db, alice.id) == Decimal(credited)
    settled = await get_deposit(client, alice, deposit["deposit_id"])
    assert settled["status"] == status
    assert (settled["paid_amount"], settled["fee"], settled["credited_amount"]) == (
        paid,
        fee,
        credited,
    )


async def test_expired_webhook_closes_without_credit(client, db, make_account):
    alice = await make_account()
    deposit = await pending_deposit(client, alice)

    response = await send(client, make_event(deposit["invoice_id"], "expired", "0.00"))

    assert response.json() == {"status": "processed"}
    assert await get_balance(db, alice.id) == Decimal("0.00")
    assert (await get_deposit(client, alice, deposit["deposit_id"]))["status"] == "expired"


async def test_wrong_secret_is_rejected(client, db, make_account):
    alice = await make_account()
    deposit = await pending_deposit(client, alice)

    response = await send(client, make_event(deposit["invoice_id"]), secret="not-the-secret")

    assert_error(response, 401, "invalid_signature")
    assert await get_balance(db, alice.id) == Decimal("0.00")


async def test_tampered_body_is_rejected(client, db, make_account):
    alice = await make_account()
    deposit = await pending_deposit(client, alice)
    body, headers = signed_webhook(make_event(deposit["invoice_id"], paid_amount="1.00"))
    tampered = body.replace(b'"1.00"', b'"999.00"')

    response = await client.post(WEBHOOK_URL, content=tampered, headers=headers)

    assert_error(response, 401, "invalid_signature")
    assert await get_balance(db, alice.id) == Decimal("0.00")


@pytest.mark.parametrize("skew", [-301, 301, -100_000])
async def test_stale_or_future_timestamp_is_rejected(client, db, make_account, skew):
    alice = await make_account()
    deposit = await pending_deposit(client, alice)

    event = make_event(deposit["invoice_id"])
    response = await send(client, event, timestamp=int(time.time()) + skew)

    assert_error(response, 401, "stale_timestamp")
    assert await get_balance(db, alice.id) == Decimal("0.00")


async def test_timestamp_inside_tolerance_is_accepted(client, make_account):
    alice = await make_account()
    deposit = await pending_deposit(client, alice)
    event = make_event(deposit["invoice_id"])
    response = await send(client, event, timestamp=int(time.time()) - 290)
    assert response.status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Timestamp": "1700000000"},
        {"X-Signature": "sha256=00"},
        {"X-Timestamp": "not-a-number", "X-Signature": "sha256=00"},
    ],
)
async def test_missing_or_garbled_headers_are_rejected(client, headers):
    response = await client.post(WEBHOOK_URL, content=b"{}", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] in {"invalid_signature", "stale_timestamp"}


async def test_webhook_does_not_need_api_key(client, make_account):
    alice = await make_account()
    deposit = await pending_deposit(client, alice)
    response = await send(client, make_event(deposit["invoice_id"]))
    assert response.status_code == 200


async def test_duplicate_event_is_credited_once(client, db, make_account):
    alice = await make_account()
    deposit = await pending_deposit(client, alice)
    event = make_event(deposit["invoice_id"])

    first = await send(client, event)
    second = await send(client, event)

    assert first.json() == {"status": "processed"}
    assert second.status_code == 200
    assert second.json() == {"status": "duplicate"}
    assert await get_balance(db, alice.id) == Decimal("9.85")


async def test_concurrent_duplicates_are_credited_once(client, db, make_account):
    alice = await make_account()
    deposit = await pending_deposit(client, alice)
    event = make_event(deposit["invoice_id"])

    responses = await asyncio.gather(*(send(client, event) for _ in range(10)))

    outcomes = sorted(r.json()["status"] for r in responses)
    assert outcomes == ["duplicate"] * 9 + ["processed"]
    assert await get_balance(db, alice.id) == Decimal("9.85")
    async with db.transaction() as session:
        entries = await session.scalar(select(func.count()).select_from(LedgerEntry))
    assert entries == 1


async def test_event_after_deposit_is_closed_changes_nothing(client, db, make_account):
    alice = await make_account()
    deposit = await pending_deposit(client, alice)
    await send(client, make_event(deposit["invoice_id"], "expired", "0.00"))

    late = await send(client, make_event(deposit["invoice_id"], "paid", "10.00"))

    assert late.status_code == 200
    assert late.json() == {"status": "ignored"}
    assert await get_balance(db, alice.id) == Decimal("0.00")
    assert (await get_deposit(client, alice, deposit["deposit_id"]))["status"] == "expired"


async def test_second_paid_event_does_not_credit_again(client, db, make_account):
    alice = await make_account()
    deposit = await pending_deposit(client, alice)
    await send(client, make_event(deposit["invoice_id"], "paid", "10.00"))

    late = await send(client, make_event(deposit["invoice_id"], "paid", "10.00"))

    assert late.json() == {"status": "ignored"}
    assert await get_balance(db, alice.id) == Decimal("9.85")


async def test_unknown_invoice_is_404(client):
    response = await send(client, make_event("inv_unknown"))
    assert_error(response, 404, "deposit_not_found")


@pytest.mark.parametrize(
    "change",
    [
        {"paid_amount": 10.0},
        {"paid_amount": "10.001"},
        {"status": "pending"},
        {"event_id": ""},
    ],
)
async def test_signed_but_invalid_payload_is_422(client, make_account, change):
    alice = await make_account()
    deposit = await pending_deposit(client, alice)
    response = await send(client, {**make_event(deposit["invoice_id"]), **change})
    assert_error(response, 422, "validation_error")


async def test_signed_non_json_is_400(client):
    body = b"not json"
    ts = str(int(time.time()))
    response = await client.post(
        WEBHOOK_URL, content=body, headers={"X-Timestamp": ts, "X-Signature": sign(body, ts)}
    )
    assert_error(response, 400, "malformed_json")


async def test_raw_body_is_verified_not_reserialized(client, db, make_account):
    """The signature is checked over the raw bytes, not over re-serialized JSON."""
    alice = await make_account()
    deposit = await pending_deposit(client, alice)
    event = make_event(deposit["invoice_id"])
    body = json.dumps(dict(reversed(event.items())), indent=2).encode()
    ts = str(int(time.time()))
    response = await client.post(
        WEBHOOK_URL,
        content=body,
        headers={"X-Timestamp": ts, "X-Signature": sign(body, ts)},
    )
    assert response.status_code == 200
    assert await get_balance(db, alice.id) == Decimal("9.85")


async def test_unknown_extra_fields_are_accepted_but_not_stored(client, db, make_account):
    alice = await make_account()
    deposit = await pending_deposit(client, alice)
    event = make_event(deposit["invoice_id"], extra_nan=float("nan"), extra_nul="a\u0000b")

    response = await send(client, event)

    assert response.json() == {"status": "processed"}
    assert await get_balance(db, alice.id) == Decimal("9.85")


async def test_nul_in_event_id_is_422(client, make_account):
    alice = await make_account()
    deposit = await pending_deposit(client, alice)
    response = await send(client, {**make_event(deposit["invoice_id"]), "event_id": "evt\u0000"})
    assert_error(response, 422, "validation_error")


@pytest.mark.parametrize("timestamp", ["1" + "0" * 400, "9" * 5000])
async def test_absurd_timestamp_is_401_not_500(client, timestamp):
    response = await client.post(
        WEBHOOK_URL,
        content=b"{}",
        headers={"X-Timestamp": timestamp, "X-Signature": sign(b"{}", timestamp)},
    )
    assert_error(response, 401, "invalid_signature")
