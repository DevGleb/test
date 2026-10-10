"""Request and response bodies, with money as "0.00" strings."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from wallet.models import Account
from wallet.money import format_money


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountOut(BaseModel):
    account_id: uuid.UUID
    name: str
    balance_usd: str
    credits: int
    created_at: datetime

    @classmethod
    def of(cls, account: Account) -> "AccountOut":
        return cls(
            account_id=account.id,
            name=account.name,
            balance_usd=format_money(account.balance_usd),
            credits=account.credits,
            created_at=account.created_at,
        )
