"""In-memory imitation of an external payment provider."""

import hashlib
import hmac
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

import httpx
from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import AnyHttpUrl, BaseModel, BeforeValidator, Field
from pydantic_settings import BaseSettings
from starlette.exceptions import HTTPException as StarletteHTTPException


class Settings(BaseSettings):
    provider_api_key: str
    webhook_secret: str
    public_base_url: str = "http://localhost:8001"
    webhook_timeout_seconds: float = 5.0


class InvoiceStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    PARTIALLY_PAID = "partially_paid"
    EXPIRED = "expired"


FINAL_STATUSES = {InvoiceStatus.PAID, InvoiceStatus.PARTIALLY_PAID, InvoiceStatus.EXPIRED}

_MONEY_RE = re.compile(r"(0|[1-9][0-9]{0,11})(\.[0-9]{1,2})?")


def _parse_money(value: object) -> Decimal:
    if not isinstance(value, str) or not _MONEY_RE.fullmatch(value):
        raise ValueError('must be a decimal string with at most 2 decimal places, e.g. "10.00"')
    return Decimal(value).quantize(Decimal("0.01"))


Money = Annotated[Decimal, BeforeValidator(_parse_money)]


def sign_webhook(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode(), str(timestamp).encode() + b"." + body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


@dataclass
class Invoice:
    invoice_id: str
    external_id: str
    amount: Decimal
    currency: str
    callback_url: str
    pay_url: str
    status: InvoiceStatus = InvoiceStatus.PENDING
    paid_amount: Decimal = Decimal("0.00")
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_json(self) -> dict:
        return {
            "invoice_id": self.invoice_id,
            "external_id": self.external_id,
            "amount": str(self.amount),
            "currency": self.currency,
            "status": self.status.value,
            "paid_amount": str(self.paid_amount),
            "pay_url": self.pay_url,
            "created_at": self.created_at.isoformat(),
        }


class CreateInvoiceRequest(BaseModel):
    amount: Money = Field(gt=0)
    currency: Literal["USD"]
    external_id: str = Field(min_length=1, max_length=128)
    callback_url: AnyHttpUrl


class SimulateRequest(BaseModel):
    status: Literal["paid", "partially_paid", "expired"]
    paid_amount: Money | None = None
    deliver_webhook: bool = True
    duplicate: bool = False


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: dict | None = None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def _error(status_code: int, code: str, message: str, details: dict | None = None) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "details": details or {}}}
    return JSONResponse(status_code=status_code, content=body)


def create_app(
    settings: Settings | None = None,
    webhook_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="provider-mock")
    invoices: dict[str, Invoice] = {}
    by_external_id: dict[str, str] = {}

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _error(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        return _error(422, "validation_error", "Request validation failed", {"errors": errors})

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error(exc.status_code, "http_error", str(exc.detail))

    def require_api_key(authorization: str | None = Header(default=None)) -> None:
        expected = f"Bearer {settings.provider_api_key}"
        if authorization is None or not hmac.compare_digest(
            authorization.encode(), expected.encode()
        ):
            raise ApiError(401, "unauthorized", "Missing or invalid bearer token")

    def get_invoice(invoice_id: str) -> Invoice:
        invoice = invoices.get(invoice_id)
        if invoice is None:
            raise ApiError(404, "invoice_not_found", "Invoice not found")
        return invoice

    async def deliver_webhook(invoice: Invoice, times: int) -> dict:
        event = {
            "event_id": f"evt_{uuid.uuid4().hex}",
            "invoice_id": invoice.invoice_id,
            "status": invoice.status.value,
            "paid_amount": str(invoice.paid_amount),
            "occurred_at": datetime.now(UTC).isoformat(),
        }
        body = json.dumps(event, separators=(",", ":")).encode()
        timestamp = int(time.time())
        headers = {
            "Content-Type": "application/json",
            "X-Timestamp": str(timestamp),
            "X-Signature": sign_webhook(settings.webhook_secret, timestamp, body),
        }
        deliveries: list[dict] = []
        async with httpx.AsyncClient(
            timeout=settings.webhook_timeout_seconds, transport=webhook_transport
        ) as client:
            for _ in range(times):
                try:
                    response = await client.post(
                        invoice.callback_url, content=body, headers=headers
                    )
                    deliveries.append({"status_code": response.status_code})
                except httpx.HTTPError as exc:
                    deliveries.append({"error": f"{type(exc).__name__}: {exc}"})
        return {"event_id": event["event_id"], "deliveries": deliveries}

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.post("/invoices", status_code=201, dependencies=[Depends(require_api_key)])
    async def create_invoice(body: CreateInvoiceRequest) -> JSONResponse:
        existing_id = by_external_id.get(body.external_id)
        if existing_id is not None:
            existing = invoices[existing_id]
            if existing.amount != body.amount or existing.callback_url != str(body.callback_url):
                raise ApiError(
                    409, "external_id_conflict", "external_id already used with other parameters"
                )
            return JSONResponse(status_code=200, content=existing.to_json())

        invoice_id = f"inv_{uuid.uuid4().hex}"
        invoice = Invoice(
            invoice_id=invoice_id,
            external_id=body.external_id,
            amount=body.amount,
            currency=body.currency,
            callback_url=str(body.callback_url),
            pay_url=f"{settings.public_base_url.rstrip('/')}/pay/{invoice_id}",
        )
        invoices[invoice_id] = invoice
        by_external_id[body.external_id] = invoice_id
        return JSONResponse(status_code=201, content=invoice.to_json())

    @app.get("/invoices/{invoice_id}", dependencies=[Depends(require_api_key)])
    async def read_invoice(invoice_id: str) -> dict:
        return get_invoice(invoice_id).to_json()

    @app.post("/invoices/{invoice_id}/simulate", dependencies=[Depends(require_api_key)])
    async def simulate(invoice_id: str, body: SimulateRequest) -> dict:
        invoice = get_invoice(invoice_id)
        if invoice.status in FINAL_STATUSES:
            raise ApiError(
                409, "invoice_final", f"Invoice is already {invoice.status.value} (final)"
            )

        new_status = InvoiceStatus(body.status)
        if new_status is InvoiceStatus.PAID:
            paid_amount = body.paid_amount if body.paid_amount is not None else invoice.amount
        elif new_status is InvoiceStatus.PARTIALLY_PAID:
            if body.paid_amount is None or not (0 < body.paid_amount < invoice.amount):
                raise ApiError(
                    422,
                    "validation_error",
                    "partially_paid needs paid_amount greater than 0 and below the invoice amount",
                )
            paid_amount = body.paid_amount
        else:
            paid_amount = Decimal("0.00")

        invoice.status = new_status
        invoice.paid_amount = paid_amount

        result = invoice.to_json()
        if body.deliver_webhook:
            result["webhook"] = await deliver_webhook(invoice, times=2 if body.duplicate else 1)
        return result

    @app.get("/pay/{invoice_id}")
    async def pay_page(invoice_id: str) -> dict:
        invoice = get_invoice(invoice_id)
        return {
            "invoice_id": invoice.invoice_id,
            "amount": str(invoice.amount),
            "currency": invoice.currency,
            "status": invoice.status.value,
            "hint": f"POST /invoices/{invoice.invoice_id}/simulate to pay this invoice",
        }

    return app


def app_factory() -> FastAPI:
    return create_app()
