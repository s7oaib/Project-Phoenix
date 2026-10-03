"""PHX-002 transport contract and isolated factory checks."""

import subprocess
import sys
from importlib.metadata import version

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from phoenix.api.app import HealthResponse, create_app


def test_create_app() -> None:
    app = create_app()
    assert isinstance(app, FastAPI)
    assert app.version == version("phoenix")
    assert create_app() is not app


def test_health_contract() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health")
        repeated_response = client.get("/health")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    expected = {"status": "ok", "service": "phoenix-api", "version": version("phoenix")}
    assert response.json() == expected
    assert repeated_response.json() == expected
    assert HealthResponse.model_validate(response.json()).model_dump() == expected


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "degraded", "service": "phoenix-api", "version": "0.1.0"},
        {"status": "ok", "service": "other", "version": "0.1.0"},
        {"status": "ok", "service": "phoenix-api", "version": ""},
        {"status": "ok", "service": "phoenix-api"},
    ],
)
def test_health_schema_rejects_invalid_contract(payload: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        HealthResponse.model_validate(payload)


def test_unknown_route() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/unknown")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_factory_is_isolated_and_uvicorn_target_is_valid() -> None:
    # A fresh interpreter prevents prior bootstrap imports from hiding coupling.
    script = """
import asyncio
import importlib.abc
import socket
import subprocess
import sys

class BlockSubsystems(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        boundaries = (
            "core", "agents", "models", "tools", "memory", "permissions",
            "voice", "recovery", "persistence",
        )
        if any(fullname == f"phoenix.{name}" or
               fullname.startswith(f"phoenix.{name}.") for name in boundaries):
            raise AssertionError(f"Unexpected subsystem import: {fullname}")

def forbidden(*args, **kwargs):
    raise AssertionError("Factory attempted network/process/background work")

sys.meta_path.insert(0, BlockSubsystems())
socket.socket.bind = forbidden
socket.socket.connect = forbidden
subprocess.Popen = forbidden
asyncio.create_task = forbidden
asyncio.BaseEventLoop.create_task = forbidden

from uvicorn.importer import import_from_string
from fastapi import FastAPI
factory = import_from_string("phoenix.api.app:create_app")
app = factory()
assert isinstance(app, FastAPI)
assert not app.router.on_startup
assert not app.router.on_shutdown
print("Factory import/create and Uvicorn target: PASS")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert result.stdout.strip() == "Factory import/create and Uvicorn target: PASS"
