"""Operational commands: python -m wallet.cli <command> --help."""

import argparse
import asyncio
import dataclasses
import json
import logging
from datetime import timedelta

from wallet.config import Settings
from wallet.db import Database
from wallet.main import build_provider_client
from wallet.models import Account
from wallet.security import generate_api_key, hash_api_key
from wallet.services.reconcile import reconcile_pending_deposits


async def create_account(db: Database, name: str) -> dict:
    api_key = generate_api_key()
    async with db.transaction() as session:
        account = Account(name=name, api_key_hash=hash_api_key(api_key))
        session.add(account)
        await session.flush()
    return {"account_id": str(account.id), "name": name, "api_key": api_key}


async def _run(args: argparse.Namespace) -> None:
    settings = Settings()
    db = Database(settings.database_url)
    try:
        if args.command == "create-account":
            print(json.dumps(await create_account(db, args.name), indent=2))
        elif args.command == "reconcile":
            await _reconcile(db, settings, args)
    finally:
        await db.dispose()


async def _reconcile(db: Database, settings: Settings, args: argparse.Namespace) -> None:
    minutes = (
        args.older_than_minutes
        if args.older_than_minutes is not None
        else settings.reconcile_after_minutes
    )
    provider = build_provider_client(settings)
    try:
        while True:
            report = await reconcile_pending_deposits(
                db, provider, older_than=timedelta(minutes=minutes)
            )
            print(json.dumps(dataclasses.asdict(report)), flush=True)
            if args.loop_seconds is None:
                break
            await asyncio.sleep(args.loop_seconds)
    finally:
        await provider.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m wallet.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-account", help="Create an account and print its API key once")
    create.add_argument("name")

    reconcile = sub.add_parser(
        "reconcile", help="Settle pending deposits whose webhook never arrived"
    )
    reconcile.add_argument(
        "--older-than-minutes",
        type=float,
        default=None,
        help="Only look at deposits pending longer than this (default: RECONCILE_AFTER_MINUTES)",
    )
    reconcile.add_argument(
        "--loop-seconds",
        type=float,
        default=None,
        help="Keep running, pausing this many seconds between passes",
    )

    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
