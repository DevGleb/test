from sqlalchemy import select

from tests.helpers import assert_error
from wallet.models import Account


async def test_me_requires_api_key(client):
    assert_error(await client.get("/v1/accounts/me"), 401, "unauthorized")


async def test_me_rejects_unknown_api_key(client, make_account):
    await make_account()
    response = await client.get("/v1/accounts/me", headers={"X-API-Key": "llk_not-a-real-key"})
    assert_error(response, 401, "unauthorized")


async def test_me_returns_balances(client, make_account):
    alice = await make_account("alice", balance="12.30")
    response = await client.get("/v1/accounts/me", headers=alice.headers)
    assert response.status_code == 200
    body = response.json()
    assert body["account_id"] == str(alice.id)
    assert body["balance_usd"] == "12.30"
    assert body["credits"] == 0


async def test_api_keys_are_not_stored_in_plaintext(db, make_account):
    alice = await make_account()
    async with db.transaction() as session:
        stored = await session.scalar(select(Account.api_key_hash).where(Account.id == alice.id))
    assert stored != alice.api_key
    assert alice.api_key not in stored


async def test_unknown_route_uses_error_format(client):
    assert_error(await client.get("/v1/nope"), 404, "not_found")


async def test_wrong_method_uses_error_format(client):
    assert_error(await client.delete("/v1/accounts/me"), 405, "method_not_allowed")
