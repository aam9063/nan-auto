"""Gateway settings, loaded from the environment and a local .env file."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration (spec 13, environment variables)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    clef_provider: str = "mock"  # "nan" | "mock"
    nan_key: str = ""
    nan_base_url: str = "https://api.nan.builders/v1"
    default_model: str = "deepseek-v4-flash"  # R0 baseline model (spec 11.4)
    decision_timeout_ms: int = 800
    clef_max_in_flight: int = 2
