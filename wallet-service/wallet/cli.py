"""Operational commands: python -m wallet.cli <command> --help."""

import argparse
import asyncio
import json

from wallet.config import Settings
from wallet.db import Database
from wallet.models import Account
from wallet.security import generate_api_key, hash_api_key


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
    finally:
        await db.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m wallet.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-account", help="Create an account and print its API key once")
    create.add_argument("name")

    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
