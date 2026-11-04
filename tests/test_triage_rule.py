"""PHX-131 pre-triage rule (ADR-225 to ADR-229): offline, no model."""

import tomllib
from pathlib import Path

import pytest

from phoenix.agents.orchestrator import OrchestrationPath
from phoenix.agents.runtime import MissionTrace
from phoenix.agents.triage import TriageOutcome, rule_decides_direct
from phoenix.persistence import missions as mission_store

ROOT = Path(__file__).resolve().parents[1]
CASES = tomllib.loads((ROOT / "benchmarks/routing/routing-v1.toml").read_text("utf-8"))[
    "case"
]


def test_rule_never_sends_a_complex_routing_case_direct() -> None:
    decided = [case for case in CASES if rule_decides_direct(case["request"])]
    assert [c["id"] for c in decided if c["label"] == "complex"] == []
    assert decided  # it does decide some simple requests


@pytest.mark.parametrize(
    "text",
    [
        "What is the capital of Japan?",
        "Who wrote the play Romeo and Juliet?",
        "Explain in one sentence what photosynthesis is.",
        "Tell me a joke.",
    ],
)
def test_rule_decides_clearly_simple_requests(text: str) -> None:
    assert rule_decides_direct(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Read notes.txt",
        "What is in todo.md?",
        "Open https://example.com",
        "Look in C:\\Users\\me",
        "Tell me about my notes.",
        "What is the weather today?",
        "Explain DNS, then explain TLS.",
        "Explain DNS and after that explain TLS.",
        "1. explain DNS 2. explain TLS",
        "Explain DNS.\nExplain TLS.",
        "What is DNS? What is TLS?",
        "Search for the capital of Japan.",
        "Click the start button.",
        "Run the backup command.",
        "Save this answer.",
        "Remember that I like tea.",
        "word " * 26,
        "a" * 201,
    ],
)
def test_rule_leaves_anything_else_to_model_triage(text: str) -> None:
    assert not rule_decides_direct(text)


def test_rule_outcome_is_distinct_and_always_direct() -> None:
    assert TriageOutcome.RULE.value == "rule"
    assert set(mission_store.TRIAGE_OUTCOMES) == {o.value for o in TriageOutcome}
    base = {"prompt_set_version": "v", "triage_outcome": "rule"}
    trace = MissionTrace.model_validate(base | {"path": "direct"})
    assert trace.path == OrchestrationPath.DIRECT
    with pytest.raises(ValueError):
        MissionTrace.model_validate(base | {"path": "complex"})
