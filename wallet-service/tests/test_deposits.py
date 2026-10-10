import json

import pytest
from sqlalchemy import select

from tests.helpers import assert_error
from wallet.models import Deposit


async def test_create_deposit_creates_provider_invoice(client, make_account, fake_provider):
    alice = await make_account()
    response = await client.post("/v1/deposits", json={"amount": "10.00"}, headers=alice.headers)

    assert response.status_code == 201, response.text
    deposit = response.json()
    assert deposit["status"] == "pending"
    assert deposit["amount"] == "10.00"
    assert deposit["pay_url"].startswith("http://provider.test/pay/inv_")

    [request] = fake_provider.requests
    sent = json.loads(request.content)
    assert request.headers["Authorization"] == "Bearer test-provider-key"
    assert sent == {
        "amount": "10.00",
        "currency": "USD",
        "external_id": deposit["deposit_id"],
        "callback_url": "http://wallet.test/v1/webhooks/provider",
    }
    assert deposit["invoice_id"] in fake_provider.invoices


async def test_get_deposit_is_scoped_to_owner(client, make_account):
    alice = await make_account("alice")
    bob = await make_account("bob")
    created = (
        await client.post("/v1/deposits", json={"amount": "5"}, headers=alice.headers)
    ).json()

    url = f"/v1/deposits/{created['deposit_id']}"
    own = await client.get(url, headers=alice.headers)
    assert own.status_code == 200
    assert own.json()["amount"] == "5.00"
    assert_error(await client.get(url, headers=bob.headers), 404, "deposit_not_found")


@pytest.mark.parametrize(
    "body",
    [
        {"amount": 10},
        {"amount": 10.5},
        {"amount": "10.001"},
        {"amount": "0.00"},
        {"amount": "-1.00"},
        {},
        {"amount": "10.00", "currency": "USD"},
    ],
)
async def test_create_deposit_validates_amount(client, make_account, fake_provider, body):
    alice = await make_account()
    response = await client.post("/v1/deposits", json=body, headers=alice.headers)
    assert_error(response, 422, "validation_error")
    assert fake_provider.requests == []


async def test_malformed_json_is_400(client, make_account):
    alice = await make_account()
    response = await client.post(
        "/v1/deposits",
        content=b'{"amount": ',
        headers={**alice.headers, "Content-Type": "application/json"},
    )
    assert_error(response, 400, "malformed_json")


@pytest.mark.parametrize("failure", ["timeout", "connect", "http500"])
async def test_provider_unavailable_is_503_and_retried(
    client, db, make_account, fake_provider, failure
):
    fake_provider.failure = failure
    alice = await make_account()
    response = await client.post("/v1/deposits", json={"amount": "10.00"}, headers=alice.headers)

    error = assert_error(response, 503, "provider_unavailable")
    assert len(fake_provider.requests) == 2
    async with db.transaction() as session:
        deposit = await session.scalar(select(Deposit))
    assert str(deposit.id) == error["details"]["deposit_id"]
    assert deposit.status == "failed"
    assert deposit.invoice_id is None


async def test_provider_rejection_is_502_without_retry(client, make_account, fake_provider):
    fake_provider.failure = "http400"
    alice = await make_account()
    response = await client.post("/v1/deposits", json={"amount": "10.00"}, headers=alice.headers)
    assert_error(response, 502, "provider_error")
    assert len(fake_provider.requests) == 1
