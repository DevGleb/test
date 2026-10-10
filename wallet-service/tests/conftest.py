"""Test fixtures backed by a real Postgres database."""

import asyncio
import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import make_url, text, update
from sqlalchemy.ext.asyncio import create_async_engine

from wallet.cli import create_account
from wallet.config import Settings
from wallet.db import Database
from wallet.main import create_app
from wallet.models import Account, Base

SERVICE_ROOT = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://ledgerlite:ledgerlite@localhost:55432/ledgerlite_test",
)
PROVIDER_API_KEY = "test-provider-key"
WEBHOOK_SECRET = "test-webhook-secret"


async def _ensure_database(url: str) -> None:
    sa_url = make_url(url)
    engine = create_async_engine(sa_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        exists = await conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": sa_url.database}
        )
        if not exists:
            await conn.execute(text(f'CREATE DATABASE "{sa_url.database}"'))
    await engine.dispose()


@pytest.fixture(scope="session")
def database_url() -> str:
    name = make_url(TEST_DATABASE_URL).database or ""
    if not name.endswith("_test"):
        pytest.exit(f"Refusing to run destructive tests against database {name!r}", returncode=2)
    asyncio.run(_ensure_database(TEST_DATABASE_URL))

    config = Config(str(SERVICE_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")
    return TEST_DATABASE_URL


@pytest.fixture
async def db(database_url):
    database = Database(database_url)
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    async with database.engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield database
    await database.dispose()


@pytest.fixture
def settings(database_url) -> Settings:
    return Settings(
        database_url=database_url,
        provider_base_url="http://provider.test",
        provider_api_key=PROVIDER_API_KEY,
        webhook_secret=WEBHOOK_SECRET,
        webhook_callback_url="http://wallet.test/v1/webhooks/provider",
        rate_limit_per_minute=10_000,
    )


@pytest.fixture
async def app(db, settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://wallet.test") as c:
        yield c


@dataclass
class AccountCreds:
    id: uuid.UUID
    api_key: str

    @property
    def headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key}


@pytest.fixture
def make_account(db):
    async def _make(name: str = "alice", balance: str = "0.00") -> AccountCreds:
        info = await create_account(db, name)
        account_id = uuid.UUID(info["account_id"])
        if Decimal(balance):
            async with db.transaction() as session:
                await session.execute(
                    update(Account).where(Account.id == account_id).values(balance_usd=balance)
                )
        return AccountCreds(id=account_id, api_key=info["api_key"])

    return _make


async def get_balance(db: Database, account_id: uuid.UUID) -> Decimal:
    async with db.transaction() as session:
        account = await session.get_one(Account, account_id)
    return account.balance_usd


def signed_webhook(
    event: dict, *, secret: str = WEBHOOK_SECRET, timestamp: int | None = None
) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(event).encode()
    ts = str(int(time.time()) if timestamp is None else timestamp)
    mac = hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Timestamp": ts,
        "X-Signature": f"sha256={mac}",
    }
    return body, headers
