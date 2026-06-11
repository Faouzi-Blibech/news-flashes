"""Application settings loaded from environment / .env file."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for news-flashes.

    All fields can be overridden via environment variables or a `.env` file
    placed at the repo root.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Claude / Anthropic
    anthropic_api_key: str = ""
    model_default: str = "claude-sonnet-4-6"
    model_highstakes: str = "claude-opus-4-8"

    # FX basket
    basket_currencies: list[str] = ["USD", "EUR", "JPY", "TND"]

    # Database
    db_path: str = "news_flashes.db"

    # Email / Brevo
    sender_from_email: str = "flashes@example.com"
    sender_from_name: str = "Desk FX"
    brevo_api_key: str = ""


# Module-level singleton — import this everywhere.
settings = Settings()
