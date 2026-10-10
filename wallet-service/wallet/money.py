"""Money rules: amounts are Decimal in code and strings on the wire, never floats."""

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from pydantic import AfterValidator, BeforeValidator, WithJsonSchema

CENT = Decimal("0.01")
ZERO = Decimal("0.00")

PROVIDER_FEE_RATE = Decimal("0.015")

CREDIT_BASE_PRICE = Decimal("0.10")
CREDIT_DISCOUNT_TIERS = (
    (1000, Decimal("0.125")),
    (500, Decimal("0.05")),
)

MIN_AMOUNT = CENT
MAX_AMOUNT = Decimal("1000000.00")
MAX_CREDITS_PER_PURCHASE = 1_000_000

_MONEY_RE = re.compile(r"(0|[1-9][0-9]{0,11})(\.[0-9]{1,2})?")


def to_cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def parse_money(value: object) -> Decimal:
    """Parse a JSON money string such as "10.50"; anything else raises ValueError."""
    if not isinstance(value, str):
        raise ValueError('must be a string such as "10.00", not a number')
    if not _MONEY_RE.fullmatch(value):
        raise ValueError("must be a non-negative decimal with at most 2 decimal places")
    return Decimal(value).quantize(CENT)


def format_money(value: Decimal) -> str:
    return str(value.quantize(CENT))


def provider_fee(paid: Decimal) -> Decimal:
    return to_cents(paid * PROVIDER_FEE_RATE)


def deposit_split(paid: Decimal) -> tuple[Decimal, Decimal]:
    """Return (fee, amount to credit), rounding the fee before subtracting it."""
    fee = provider_fee(paid)
    return fee, paid - fee


def credits_price(credits: int) -> Decimal:
    if credits <= 0:
        raise ValueError("credits must be positive")
    discount = next((d for threshold, d in CREDIT_DISCOUNT_TIERS if credits >= threshold), ZERO)
    return to_cents(credits * CREDIT_BASE_PRICE * (1 - discount))


def _check_operation_amount(value: Decimal) -> Decimal:
    if value < MIN_AMOUNT:
        raise ValueError(f"must be at least {MIN_AMOUNT}")
    if value > MAX_AMOUNT:
        raise ValueError(f"must be at most {MAX_AMOUNT}")
    return value


_MONEY_SCHEMA = WithJsonSchema(
    {"type": "string", "pattern": r"^(0|[1-9][0-9]{0,11})(\.[0-9]{1,2})?$", "examples": ["10.00"]}
)

MoneyStr = Annotated[Decimal, BeforeValidator(parse_money), _MONEY_SCHEMA]
AmountStr = Annotated[
    Decimal, BeforeValidator(parse_money), AfterValidator(_check_operation_amount), _MONEY_SCHEMA
]
