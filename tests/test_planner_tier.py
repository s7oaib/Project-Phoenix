"""PHX-134 (ADR-240, ADR-241): the planner tier flag ``PHOENIX_PLANNER_TIER``."""

import pytest

from phoenix.agents.runtime import (
    DEFAULT_PLANNER_TIER,
    PLANNER_TIER_ENV,
    default_tier_table,
    planner_tier,
)

T2_FIRST = ["qwen3.5:9b", "qwen3.5:4b", "qwen3.5:2b"]
T1_FIRST = ["qwen3.5:4b", "qwen3.5:2b"]
PLAN_ROLES = ("strategist", "assignment")


def models(tier: str | None, role: str) -> list[str]:
    return [ref.model_id for ref in default_tier_table(tier).candidates(role)]


def test_default_tier_is_9b(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PLANNER_TIER_ENV, raising=False)
    assert DEFAULT_PLANNER_TIER == "9b"
    assert planner_tier() == "9b"
    for role in PLAN_ROLES:
        assert models(None, role) == T2_FIRST


@pytest.mark.parametrize("value", ["4b", " 4B ", "4b\n"])
def test_env_4b_moves_only_planning_roles_to_t1(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(PLANNER_TIER_ENV, value)
    assert planner_tier() == "4b"
    nine = default_tier_table("9b")
    four = default_tier_table()
    for role in PLAN_ROLES:
        assert models(None, role) == T1_FIRST
    # LEVI, PHOENIX, triage, the coder and KAIDO are untouched.
    for role in nine.roles:
        if role not in PLAN_ROLES:
            assert four.candidates(role) == nine.candidates(role), role
    assert four.default == nine.default
    assert four.candidates("verifier")[0].model_id == "qwen3.5:9b"


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_env_is_the_default(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(PLANNER_TIER_ENV, value)
    assert planner_tier() == DEFAULT_PLANNER_TIER


@pytest.mark.parametrize("value", ["2b", "9", "qwen3.5:4b", "auto"])
def test_unknown_env_value_is_refused(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(PLANNER_TIER_ENV, value)
    with pytest.raises(ValueError, match=PLANNER_TIER_ENV):
        planner_tier()
    with pytest.raises(ValueError):
        default_tier_table()


def test_explicit_argument_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PLANNER_TIER_ENV, "4b")
    assert models("9b", "strategist") == T2_FIRST
    monkeypatch.setenv(PLANNER_TIER_ENV, "9b")
    assert models("4b", "assignment") == T1_FIRST
    with pytest.raises(ValueError):
        default_tier_table("7b")
