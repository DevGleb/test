import hashlib
import hmac
import secrets
import time

from wallet.errors import AppError

API_KEY_PREFIX = "llk_"


def generate_api_key() -> str:
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


def webhook_signature(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def verify_webhook_signature(
    *,
    secret: str,
    timestamp: str | None,
    signature: str | None,
    body: bytes,
    tolerance_seconds: int,
    now: float | None = None,
) -> None:
    """Raise a 401 unless the HMAC over the raw body is valid and the timestamp is fresh."""
    if not timestamp or not signature:
        raise AppError(401, "invalid_signature", "Missing X-Timestamp or X-Signature header")
    if not timestamp.isascii() or not timestamp.isdigit():
        raise AppError(401, "invalid_signature", "X-Timestamp must be a unix timestamp in seconds")

    now = time.time() if now is None else now
    if abs(now - int(timestamp)) > tolerance_seconds:
        raise AppError(401, "stale_timestamp", "X-Timestamp is outside the allowed window")

    expected = webhook_signature(secret, timestamp, body)
    if not hmac.compare_digest(expected.encode(), signature.strip().lower().encode()):
        raise AppError(401, "invalid_signature", "Webhook signature does not match")
