import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from wallet.api import accounts, deposits, purchases, transactions, transfers, webhooks
from wallet.config import Settings
from wallet.db import Database
from wallet.errors import install_error_handlers
from wallet.provider_client import ProviderClient


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.db = Database(settings.database_url)
        app.state.provider = build_provider_client(settings)
        try:
            yield
        finally:
            await app.state.provider.aclose()
            await app.state.db.dispose()

    app = FastAPI(title="wallet-service", lifespan=lifespan)
    app.state.settings = settings
    install_error_handlers(app)

    @app.get("/health", include_in_schema=False)
    async def health() -> dict:
        return {"status": "ok"}

    app.include_router(accounts.router)
    app.include_router(deposits.router)
    app.include_router(webhooks.router)
    app.include_router(transfers.router)
    app.include_router(purchases.router)
    app.include_router(transactions.router)
    return app


def build_provider_client(settings: Settings, **kwargs) -> ProviderClient:
    return ProviderClient(
        base_url=settings.provider_base_url,
        api_key=settings.provider_api_key,
        timeout_seconds=settings.provider_timeout_seconds,
        connect_timeout_seconds=settings.provider_connect_timeout_seconds,
        max_attempts=settings.provider_max_attempts,
        **kwargs,
    )


def app_factory() -> FastAPI:
    logging.basicConfig(level=logging.INFO)
    return create_app()
