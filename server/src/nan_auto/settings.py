"""Gateway settings, loaded from the environment and a local .env file."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration (spec 13, environment variables)."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    clef_provider: str = "mock"  # "nan" | "mock"
    nan_key: str = ""
    nan_base_url: str = "https://api.nan.builders/v1"
    default_model: str = "deepseek-v4-flash"  # R0 baseline model (spec 11.4)
    decision_timeout_ms: int = 800
    clef_max_in_flight: int = 2
    # Model completions routinely take longer than httpx's 5 s default, which
    # surfaces as a transport error and a 502. Long by design: the decision has
    # its own budget (decision_timeout_ms); this one is for generating tokens.
    upstream_timeout_s: float = 120.0
    # Retry walk (spec 5.3 caps it at two retries) and circuit breaker (5.5).
    fallback_max_retries: int = 2
    breaker_threshold: int = 3
    breaker_cooldown_s: float = 60.0
    key_concurrency_limit: int = 8
    quota_cooldown_s: float = 3600.0
