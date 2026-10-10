"""Plain helpers shared by tests."""

import hashlib
import hmac
import json
import time
import uuid
from decimal import Decimal

from wallet.db import Database
from wallet.models import Account

PROVIDER_API_KEY = "test-provider-key"
WEBHOOK_SECRET = "test-webhook-secret"


def assert_error(response, status_code: int, code: str) -> dict:
    assert response.status_code == status_code, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == code
    return body["error"]


async def get_balance(db: Database, account_id: uuid.UUID) -> Decimal:
    async with db.transaction() as session:
        account = await session.get_one(Account, account_id)
    return account.balance_usd


def sign(body: bytes, timestamp: str, secret: str = WEBHOOK_SECRET) -> str:
    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def signed_webhook(
    event: dict, *, secret: str = WEBHOOK_SECRET, timestamp: int | None = None
) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(event).encode()
    ts = str(int(time.time()) if timestamp is None else timestamp)
    headers = {
        "Content-Type": "application/json",
        "X-Timestamp": ts,
        "X-Signature": sign(body, ts, secret),
    }
    return body, headers
