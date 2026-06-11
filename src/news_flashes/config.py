"""Application settings loaded from environment / .env file.

Unified settings for both pipeline halves: Person A's ingestion data sources
and scheduler, plus Person B's Claude generation and email delivery. Exposes
both a ``settings`` singleton (used by most modules) and ``get_settings()``
(used by the DB layer).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration. Override any field via env vars or a ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Claude / Anthropic (generation) ---
    anthropic_api_key: str = ""
    model_default: str = "claude-sonnet-4-6"
    model_highstakes: str = "claude-opus-4-8"

    # --- Ingestion data sources (signals) ---
    forex_factory_url: str = (
        "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    )
    news_api_key: str = ""
    market_data_api_key: str = ""

    # --- Email / delivery ---
    brevo_api_key: str = ""
    sender_from_email: str = "flashes@example.com"
    sender_from_name: str = "Desk FX"

    # --- Database ---
    database_url: str = "sqlite:///news_flashes.db"

    # --- Scheduling ---
    poll_interval_minutes: int = 15

    # --- Shared ---
    basket_currencies: list[str] = ["USD", "EUR", "JPY", "TND"]

    @field_validator("basket_currencies", mode="before")
    @classmethod
    def _parse_basket(cls, v: object) -> list[str]:
        """Allow a comma-separated string in the env var (e.g. "USD,EUR,TND")."""
        if isinstance(v, str):
            return [c.strip() for c in v.split(",") if c.strip()]
        return list(v)  # type: ignore[arg-type]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached settings singleton."""
    return Settings()


# Module-level singleton — import this everywhere.
settings: Settings = get_settings()
