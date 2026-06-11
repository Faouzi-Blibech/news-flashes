"""Application settings loaded from environment / .env file."""

from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- AI ---
    anthropic_api_key: str = ""

    # --- Data sources ---
    forex_factory_url: str = (
        "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    )
    news_api_key: str = ""
    market_data_api_key: str = ""

    # --- Email delivery ---
    brevo_api_key: str = ""

    # --- Storage ---
    database_url: str = "sqlite:///news_flashes.db"

    # --- Scheduler ---
    poll_interval_minutes: int = 15

    # --- Currency basket (stored as raw comma string, exposed as list) ---
    basket_currencies: list[str] = ["USD", "EUR", "JPY", "TND"]

    @field_validator("basket_currencies", mode="before")
    @classmethod
    def _parse_basket(cls, v: object) -> list[str]:
        if isinstance(v, str):
            return [c.strip() for c in v.split(",") if c.strip()]
        return list(v)  # type: ignore[arg-type]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings singleton."""
    return Settings()


# Module-level convenience alias — import as `from news_flashes.config import settings`
settings: Settings = get_settings()
