"""Spoken Trust mode needs an explicit on or off word (review finding I3)."""

import asyncio

import pytest

from phoenix.security.trust import TRUST
from phoenix.sites.command import handle_command


@pytest.fixture(autouse=True)
def _trust_off() -> None:
    TRUST.set(False)


@pytest.mark.parametrize(
    ("said", "enabled"),
    [
        ("trust mode on", True),
        ("Turn on trust mode.", True),
        ("enable trust mode", True),
        ("trust mode off", False),
        ("disable trust mode", False),
    ],
)
def test_explicit_words_switch_trust(said: str, enabled: bool) -> None:
    TRUST.set(not enabled)
    out = asyncio.run(handle_command(said))
    assert out.kind == "site" and TRUST.enabled is enabled


@pytest.mark.parametrize("said", ["trust mode", "the trust mode", "Trust mode."])
def test_bare_trust_mode_changes_nothing(said: str) -> None:
    out = asyncio.run(handle_command(said))
    assert out.kind == "passthrough" and TRUST.enabled is False
