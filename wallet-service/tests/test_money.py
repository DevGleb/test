from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from wallet.money import AmountStr, MoneyStr, credits_price, deposit_split, parse_money


@pytest.mark.parametrize(
    ("paid", "fee", "credited"),
    [
        ("10.00", "0.15", "9.85"),
        ("0.99", "0.01", "0.98"),
        ("33.33", "0.50", "32.83"),
        ("1.00", "0.02", "0.98"),
        ("0.01", "0.00", "0.01"),
        ("7.50", "0.11", "7.39"),
    ],
)
def test_deposit_fee(paid, fee, credited):
    assert deposit_split(Decimal(paid)) == (Decimal(fee), Decimal(credited))


@pytest.mark.parametrize(
    ("credits", "price"),
    [
        (333, "33.30"),
        (507, "48.17"),
        (1005, "87.94"),
        (1, "0.10"),
        (499, "49.90"),
        (500, "47.50"),
        (999, "94.91"),
        (1000, "87.50"),
    ],
)
def test_credits_price(credits, price):
    assert credits_price(credits) == Decimal(price)


@pytest.mark.parametrize("credits", [0, -1])
def test_credits_price_rejects_non_positive(credits):
    with pytest.raises(ValueError):
        credits_price(credits)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("10.00", "10.00"), ("10", "10.00"), ("10.5", "10.50"), ("0.01", "0.01"), ("0", "0.00")],
)
def test_parse_money_valid(raw, expected):
    parsed = parse_money(raw)
    assert parsed == Decimal(expected)
    assert str(parsed) == expected


@pytest.mark.parametrize(
    "raw",
    [
        10,
        10.5,
        True,
        None,
        "10.001",
        "10.100",
        "-5.00",
        "+5.00",
        "1_000.00",
        " 10.00",
        "10.00\n",
        "1e3",
        "NaN",
        "Infinity",
        "010.00",
        ".50",
        "10.",
        "",
        "\u0661\u0660",
        "1000000000000.00",
    ],
)
def test_parse_money_invalid(raw):
    with pytest.raises(ValueError):
        parse_money(raw)


class _Body(BaseModel):
    amount: AmountStr


class _Event(BaseModel):
    paid_amount: MoneyStr


def test_amount_field_rejects_numbers_and_out_of_range():
    assert _Body.model_validate({"amount": "5.00"}).amount == Decimal("5.00")
    for bad in (5, 5.0, "0.00", "1000000.01", "5.001"):
        with pytest.raises(ValidationError):
            _Body.model_validate({"amount": bad})
    with pytest.raises(ValidationError):
        _Body.model_validate_json('{"amount": 5.00}')


def test_money_field_allows_zero():
    assert _Event.model_validate({"paid_amount": "0.00"}).paid_amount == Decimal("0.00")
