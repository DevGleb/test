"""Test fixtures backed by a real Postgres database."""

import asyncio
import json
import os
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

from tests.helpers import PROVIDER_API_KEY, WEBHOOK_SECRET
from wallet.cli import create_account
from wallet.config import Settings
from wallet.db import Database
from wallet.main import build_provider_client, create_app
from wallet.models import Account, Base

SERVICE_ROOT = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://ledgerlite:ledgerlite@localhost:55432/ledgerlite_test",
)


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


class FakeProvider:
    """Stands in for provider-mock behind httpx.MockTransport."""

    def __init__(self) -> None:
        self.invoices: dict[str, dict] = {}
        self.requests: list[httpx.Request] = []
        self.failure: str | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.failure == "timeout":
            raise httpx.ReadTimeout("read timed out", request=request)
        if self.failure == "connect":
            raise httpx.ConnectError("connection refused", request=request)
        if self.failure == "http500":
            return httpx.Response(500, json={"error": "boom"})
        if self.failure == "http400":
            return httpx.Response(400, json={"error": "bad request"})
        if request.headers.get("Authorization") != f"Bearer {PROVIDER_API_KEY}":
            return httpx.Response(401)

        path = request.url.path
        if request.method == "POST" and path == "/invoices":
            data = json.loads(request.content)
            invoice_id = f"inv_{uuid.uuid4().hex}"
            self.invoices[invoice_id] = {
                "invoice_id": invoice_id,
                "external_id": data["external_id"],
                "amount": data["amount"],
                "currency": data["currency"],
                "callback_url": data["callback_url"],
                "status": "pending",
                "paid_amount": "0.00",
                "pay_url": f"http://provider.test/pay/{invoice_id}",
            }
            return httpx.Response(201, json=self.invoices[invoice_id])
        if request.method == "GET" and path.startswith("/invoices/"):
            invoice = self.invoices.get(path.removeprefix("/invoices/"))
            return httpx.Response(200, json=invoice) if invoice else httpx.Response(404)
        return httpx.Response(404)

    def set_status(self, invoice_id: str, status: str, paid_amount: str = "0.00") -> None:
        self.invoices[invoice_id].update(status=status, paid_amount=paid_amount)


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def provider_client(settings, fake_provider):
    return build_provider_client(
        settings, transport=httpx.MockTransport(fake_provider), retry_backoff_seconds=0
    )


@pytest.fixture
async def app(db, settings, provider_client):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        await app.state.provider.aclose()
        app.state.provider = provider_client
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
