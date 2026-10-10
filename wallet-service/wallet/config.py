from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Configuration from environment variables; secrets have no defaults."""

    database_url: str
    provider_base_url: str
    provider_api_key: str
    webhook_secret: str
    webhook_callback_url: str

    provider_timeout_seconds: float = 5.0
    provider_connect_timeout_seconds: float = 2.0
    provider_max_attempts: int = 2

    webhook_tolerance_seconds: int = 300
    rate_limit_per_minute: int = 30
    reconcile_after_minutes: int = 10
