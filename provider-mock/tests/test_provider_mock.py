import hashlib
import hmac
import json

import httpx
import pytest

from provider_mock.main import Settings, create_app

API_KEY = "test-provider-key"
SECRET = "test-webhook-secret"
AUTH = {"Authorization": f"Bearer {API_KEY}"}
CALLBACK = "http://wallet.test/v1/webhooks/provider"


class WebhookRecorder:
    def __init__(self, status_code: int = 200, fail: bool = False):
        self.requests: list[httpx.Request] = []
        self.status_code = status_code
        self.fail = fail

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(self.status_code, json={"status": "ok"})


@pytest.fixture
def recorder() -> WebhookRecorder:
    return WebhookRecorder()


@pytest.fixture
async def client(recorder):
    settings = Settings(provider_api_key=API_KEY, webhook_secret=SECRET)
    app = create_app(settings, webhook_transport=httpx.MockTransport(recorder))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://provider.test") as c:
        yield c


async def create_invoice(client, amount="10.00", external_id="dep_1"):
    response = await client.post(
        "/invoices",
        json={
            "amount": amount,
            "currency": "USD",
            "external_id": external_id,
            "callback_url": CALLBACK,
        },
        headers=AUTH,
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": API_KEY}]
)
async def test_requires_bearer_token(client, headers):
    response = await client.post("/invoices", json={}, headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_create_and_read_invoice(client):
    invoice = await create_invoice(client)
    assert invoice["status"] == "pending"
    assert invoice["invoice_id"].startswith("inv_")
    assert invoice["pay_url"].endswith(invoice["invoice_id"])

    response = await client.get(f"/invoices/{invoice['invoice_id']}", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["paid_amount"] == "0.00"


async def test_create_is_idempotent_on_external_id(client):
    first = await create_invoice(client, external_id="dep_same")
    response = await client.post(
        "/invoices",
        json={
            "amount": "10.00",
            "currency": "USD",
            "external_id": "dep_same",
            "callback_url": CALLBACK,
        },
        headers=AUTH,
    )
    assert response.status_code == 200
    assert response.json()["invoice_id"] == first["invoice_id"]

    response = await client.post(
        "/invoices",
        json={
            "amount": "11.00",
            "currency": "USD",
            "external_id": "dep_same",
            "callback_url": CALLBACK,
        },
        headers=AUTH,
    )
    assert response.status_code == 409


@pytest.mark.parametrize(
    "body",
    [
        {"amount": 10, "currency": "USD"},
        {"amount": "10.001", "currency": "USD"},
        {"amount": "10.00", "currency": "EUR"},
        {"amount": "0.00", "currency": "USD"},
    ],
)
async def test_create_validates_body(client, body):
    response = await client.post(
        "/invoices", json={**body, "external_id": "x", "callback_url": CALLBACK}, headers=AUTH
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_simulate_paid_sends_signed_webhook(client, recorder):
    invoice = await create_invoice(client)
    response = await client.post(
        f"/invoices/{invoice['invoice_id']}/simulate", json={"status": "paid"}, headers=AUTH
    )
    assert response.status_code == 200
    assert response.json()["status"] == "paid"
    assert response.json()["webhook"]["deliveries"] == [{"status_code": 200}]

    [request] = recorder.requests
    assert str(request.url) == CALLBACK
    raw = request.content
    timestamp = request.headers["X-Timestamp"]
    expected = hmac.new(SECRET.encode(), f"{timestamp}.".encode() + raw, hashlib.sha256).hexdigest()
    assert request.headers["X-Signature"] == f"sha256={expected}"

    payload = json.loads(raw)
    assert payload["event_id"].startswith("evt_")
    assert payload["invoice_id"] == invoice["invoice_id"]
    assert payload["status"] == "paid"
    assert payload["paid_amount"] == "10.00"
    assert "occurred_at" in payload


async def test_duplicate_sends_the_same_event_twice(client, recorder):
    invoice = await create_invoice(client)
    response = await client.post(
        f"/invoices/{invoice['invoice_id']}/simulate",
        json={"status": "partially_paid", "paid_amount": "7.50", "duplicate": True},
        headers=AUTH,
    )
    assert response.status_code == 200
    first, second = recorder.requests
    assert first.content == second.content
    assert json.loads(first.content)["paid_amount"] == "7.50"


async def test_final_status_cannot_change(client):
    invoice = await create_invoice(client)
    url = f"/invoices/{invoice['invoice_id']}/simulate"
    assert (await client.post(url, json={"status": "expired"}, headers=AUTH)).status_code == 200
    response = await client.post(url, json={"status": "paid"}, headers=AUTH)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invoice_final"


async def test_no_webhook_when_disabled(client, recorder):
    invoice = await create_invoice(client)
    await client.post(
        f"/invoices/{invoice['invoice_id']}/simulate",
        json={"status": "paid", "deliver_webhook": False},
        headers=AUTH,
    )
    assert recorder.requests == []


@pytest.mark.parametrize("paid_amount", [None, "0.00", "10.00", "12.00"])
async def test_partially_paid_requires_amount_below_invoice(client, paid_amount):
    invoice = await create_invoice(client)
    body = {"status": "partially_paid"}
    if paid_amount is not None:
        body["paid_amount"] = paid_amount
    response = await client.post(
        f"/invoices/{invoice['invoice_id']}/simulate", json=body, headers=AUTH
    )
    assert response.status_code == 422


async def test_unreachable_callback_does_not_fail_simulate(client, recorder):
    recorder.fail = True
    invoice = await create_invoice(client)
    response = await client.post(
        f"/invoices/{invoice['invoice_id']}/simulate", json={"status": "paid"}, headers=AUTH
    )
    assert response.status_code == 200
    assert "ConnectError" in response.json()["webhook"]["deliveries"][0]["error"]


async def test_unknown_invoice_is_404(client):
    response = await client.get("/invoices/inv_missing", headers=AUTH)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "invoice_not_found"
