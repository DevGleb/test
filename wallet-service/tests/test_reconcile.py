import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import text

from tests.helpers import get_balance, signed_webhook
from wallet.models import Deposit
from wallet.services.reconcile import reconcile_pending_deposits

N = timedelta(minutes=10)


async def make_pending(client, account, amount="10.00") -> dict:
    response = await client.post("/v1/deposits", json={"amount": amount}, headers=account.headers)
    assert response.status_code == 201
    return response.json()


async def age(db, deposit_id, minutes: int) -> None:
    async with db.transaction() as session:
        await session.execute(
            text(
                "UPDATE deposits SET created_at = now() - make_interval(mins => :m) WHERE id = :id"
            ),
            {"m": minutes, "id": uuid.UUID(str(deposit_id))},
        )


async def status_of(db, deposit_id) -> str:
    async with db.transaction() as session:
        return (await session.get_one(Deposit, uuid.UUID(str(deposit_id)))).status


async def test_lost_webhook_is_settled_from_provider_state(
    client, db, make_account, fake_provider, provider_client
):
    alice = await make_account()
    deposit = await make_pending(client, alice)
    fake_provider.set_status(deposit["invoice_id"], "paid", "10.00")
    await age(db, deposit["deposit_id"], 11)

    report = await reconcile_pending_deposits(db, provider_client, older_than=N)

    assert (report.checked, report.settled) == (1, 1)
    assert await status_of(db, deposit["deposit_id"]) == "paid"
    assert await get_balance(db, alice.id) == Decimal("9.85")


async def test_late_webhook_after_reconciliation_is_ignored(
    client, db, make_account, fake_provider, provider_client
):
    alice = await make_account()
    deposit = await make_pending(client, alice)
    fake_provider.set_status(deposit["invoice_id"], "partially_paid", "4.00")
    await age(db, deposit["deposit_id"], 11)
    await reconcile_pending_deposits(db, provider_client, older_than=N)

    body, headers = signed_webhook(
        {
            "event_id": "evt_late",
            "invoice_id": deposit["invoice_id"],
            "status": "partially_paid",
            "paid_amount": "4.00",
            "occurred_at": "2026-01-01T00:00:00+00:00",
        }
    )
    response = await client.post("/v1/webhooks/provider", content=body, headers=headers)

    assert response.json() == {"status": "ignored"}
    assert await get_balance(db, alice.id) == Decimal("3.94")


async def test_expired_at_provider_closes_deposit(
    client, db, make_account, fake_provider, provider_client
):
    alice = await make_account()
    deposit = await make_pending(client, alice)
    fake_provider.set_status(deposit["invoice_id"], "expired")
    await age(db, deposit["deposit_id"], 11)

    await reconcile_pending_deposits(db, provider_client, older_than=N)

    assert await status_of(db, deposit["deposit_id"]) == "expired"
    assert await get_balance(db, alice.id) == Decimal("0.00")


async def test_young_or_still_pending_deposits_are_left_alone(
    client, db, make_account, fake_provider, provider_client
):
    alice = await make_account()
    young = await make_pending(client, alice)
    fake_provider.set_status(young["invoice_id"], "paid", "10.00")
    unpaid = await make_pending(client, alice)
    await age(db, unpaid["deposit_id"], 11)

    report = await reconcile_pending_deposits(db, provider_client, older_than=N)

    assert (report.checked, report.still_pending, report.settled) == (1, 1, 0)
    assert await status_of(db, young["deposit_id"]) == "pending"
    assert await status_of(db, unpaid["deposit_id"]) == "pending"


async def test_provider_errors_leave_deposit_pending(
    client, db, make_account, fake_provider, provider_client
):
    alice = await make_account()
    deposit = await make_pending(client, alice)
    await age(db, deposit["deposit_id"], 11)
    fake_provider.failure = "timeout"

    report = await reconcile_pending_deposits(db, provider_client, older_than=N)

    assert report.provider_errors == 1
    assert await status_of(db, deposit["deposit_id"]) == "pending"


async def test_deposit_without_invoice_is_failed_only_when_clearly_abandoned(
    db, make_account, provider_client
):
    alice = await make_account()
    async with db.transaction() as session:
        stuck = Deposit(account_id=alice.id, amount=Decimal("5.00"))
        recent = Deposit(account_id=alice.id, amount=Decimal("5.00"))
        session.add_all([stuck, recent])
    await age(db, stuck.id, 20)
    await age(db, recent.id, 11)

    report = await reconcile_pending_deposits(db, provider_client, older_than=N)

    assert report.failed_without_invoice == 1
    assert await status_of(db, stuck.id) == "failed"
    assert await status_of(db, recent.id) == "pending"
