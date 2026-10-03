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
