import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from wallet.api import accounts
from wallet.config import Settings
from wallet.db import Database
from wallet.errors import install_error_handlers


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.db = Database(settings.database_url)
        try:
            yield
        finally:
            await app.state.db.dispose()

    app = FastAPI(title="wallet-service", lifespan=lifespan)
    app.state.settings = settings
    install_error_handlers(app)

    @app.get("/health", include_in_schema=False)
    async def health() -> dict:
        return {"status": "ok"}

    app.include_router(accounts.router)
    return app


def app_factory() -> FastAPI:
    logging.basicConfig(level=logging.INFO)
    return create_app()
