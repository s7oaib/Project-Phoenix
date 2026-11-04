"""PHX-140 (ADR-267): web research is on by default; tests opt out unless
they set PHOENIX_WEB_RESEARCH_ENABLED (or settings) themselves."""

import pytest


@pytest.fixture(autouse=True)
def _web_research_opt_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOENIX_WEB_RESEARCH_ENABLED", "0")
