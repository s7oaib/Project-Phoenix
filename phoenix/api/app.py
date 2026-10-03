"""Minimal API transport; health describes API responsiveness only."""

from importlib.metadata import version
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Transport health, without claims about future Phoenix subsystems."""

    status: Literal["ok"]
    service: Literal["phoenix-api"]
    version: str = Field(min_length=1)


def create_app() -> FastAPI:
    """Construct the transport without initializing Phoenix subsystems."""
    app = FastAPI(title="Phoenix API", version=version("phoenix"))

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", service="phoenix-api", version=app.version)

    return app
