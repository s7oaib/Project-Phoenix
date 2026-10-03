"""Verify that the installed distribution exposes every approved boundary."""

from importlib import import_module
from importlib.metadata import metadata

import pytest


@pytest.mark.parametrize(
    "boundary",
    [
        "core",
        "agents",
        "models",
        "tools",
        "memory",
        "permissions",
        "voice",
        "recovery",
        "persistence",
        "api",
    ],
)
def test_installed_package_boundary(boundary: str) -> None:
    module = import_module(f"phoenix.{boundary}")
    assert module.__package__ == f"phoenix.{boundary}"


def test_distribution_has_foundation_runtime_dependencies() -> None:
    distribution = metadata("phoenix")
    assert set(distribution["Requires-Python"].split(",")) == {">=3.11", "<3.12"}
    requirements = distribution.get_all("Requires-Dist") or []
    assert {requirement.split("<")[0] for requirement in requirements} == {
        "alembic",
        "fastapi",
        "pydantic",
        "pydantic-settings",
        "sqlalchemy",
        "uvicorn",
    }
