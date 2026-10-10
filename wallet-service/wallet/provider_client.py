"""HTTP client for the payment provider with timeouts and safe retries."""

import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal

import httpx
from pydantic import BaseModel, ValidationError

from wallet.money import MoneyStr

logger = logging.getLogger(__name__)


class ProviderError(Exception):
    """The provider did not give a usable answer."""


class ProviderUnavailable(ProviderError):
    """Timeout, connection failure or 5xx from the provider."""


class ProviderBadResponse(ProviderError):
    """A 4xx or malformed body from the provider; retrying will not help."""


class _InvoicePayload(BaseModel):
    invoice_id: str
    status: str
    paid_amount: MoneyStr
    pay_url: str | None = None


@dataclass(frozen=True)
class ProviderInvoice:
    invoice_id: str
    status: str
    paid_amount: Decimal
    pay_url: str | None


class ProviderClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float,
        connect_timeout_seconds: float,
        max_attempts: int,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_backoff_seconds: float = 0.2,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(timeout_seconds, connect=connect_timeout_seconds),
            transport=transport,
        )
        self._max_attempts = max(1, max_attempts)
        self._backoff = retry_backoff_seconds

    async def create_invoice(
        self, *, amount: Decimal, external_id: str, callback_url: str
    ) -> ProviderInvoice:
        payload = {
            "amount": str(amount),
            "currency": "USD",
            "external_id": external_id,
            "callback_url": callback_url,
        }
        return self._parse(await self._request("POST", "/invoices", json=payload))

    async def get_invoice(self, invoice_id: str) -> ProviderInvoice:
        return self._parse(await self._request("GET", f"/invoices/{invoice_id}"))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, url: str, json: dict | None = None) -> dict:
        last_error = "unknown error"
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._client.request(method, url, json=json)
            except httpx.TransportError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code < 400:
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise ProviderBadResponse("Provider returned invalid JSON") from exc
                if response.status_code < 500:
                    raise ProviderBadResponse(
                        f"Provider rejected the request with HTTP {response.status_code}"
                    )
                last_error = f"HTTP {response.status_code}"

            logger.warning(
                "Provider %s %s failed (attempt %d/%d): %s",
                method,
                url,
                attempt,
                self._max_attempts,
                last_error,
            )
            if attempt < self._max_attempts:
                await asyncio.sleep(self._backoff * attempt)
        raise ProviderUnavailable(f"Provider unavailable: {last_error}")

    @staticmethod
    def _parse(data: dict) -> ProviderInvoice:
        try:
            invoice = _InvoicePayload.model_validate(data)
        except ValidationError as exc:
            raise ProviderBadResponse("Provider returned an unexpected invoice payload") from exc
        return ProviderInvoice(
            invoice_id=invoice.invoice_id,
            status=invoice.status,
            paid_amount=invoice.paid_amount,
            pay_url=invoice.pay_url,
        )
