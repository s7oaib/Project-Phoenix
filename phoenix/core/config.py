"""Typed local configuration, without initializing runtime subsystems."""

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Explicit values > process environment > cwd .env > portable defaults.

    Unknown constructor/dotenv keys fail validation. Process environment keys
    outside the declared fields are ignored by the standard settings source.
    Construction only reads configuration; callers own directory creation.
    """

    model_config = SettingsConfigDict(
        env_prefix="PHOENIX_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
    )

    environment: Literal["development", "test", "production"] = "development"
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    data_dir: Path = Field(default_factory=lambda: Path.home() / ".phoenix" / "data")
    # PHX-111 (ADR-135): offer web research steps (DuckDuckGo). Off until
    # PHX-108's capability settings own it; every query still needs the user's
    # grant (PHOENIX_WEB_RESEARCH_ENABLED=true enables it for a launch).
    web_research_enabled: bool = False
