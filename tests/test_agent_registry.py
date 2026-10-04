"""PHX-020 canonical identity, immutable snapshots and no-runtime evidence."""

import ast
import inspect
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from phoenix.agents import registry
from phoenix.agents.registry import (
    DEFAULT_AGENT_DEFINITIONS,
    AgentDefinition,
    AgentRegistry,
    UnknownAgentError,
    build_default_agent_registry,
)

CANONICAL_IDS = (
    "phoenix",
    "strategist",
    "intelligence",
    "automation",
    "mission_coordinator",
    "project_manager",
    "build_engineer",
    "document_analyst",
    "system_intelligence",
    "diagnostics",
    "perimeter_security",
    "health_biometrics",
    "ethics_compliance",
    "media",
    "failsafe",
    "infrastructure",
    "knowledge_rag",
    "background_optimizer",
    "scenario_analysis",
    "heavy_compute",
    "threat_recon",
    "priority_executor",
    "user_interaction",
    "silent_monitor",
    "adaptive_recovery",
    "memory",
    "web_research",
    "voice",
    "computer_control",
    "verifier",
)


def definition(
    identity: str = "custom", alias: str | None = "Display"
) -> AgentDefinition:
    return AgentDefinition(
        internal_id=identity, alias=alias, professional_role="Descriptive custom role"
    )


def test_canonical_roster_matches_frozen_document_and_order() -> None:
    agents = build_default_agent_registry().list_agents()
    assert isinstance(agents, tuple) and len(agents) == 30
    assert tuple(agent.internal_id for agent in agents) == CANONICAL_IDS
    assert len(set(CANONICAL_IDS)) == 30
    assert agents[0].internal_id == "phoenix" and agents[-1].internal_id == "verifier"
    document = Path(__file__).resolve().parents[1] / "docs/03_AGENT_ROSTER.md"
    rows = tuple(
        tuple(cell.strip() for cell in line.split("|")[1:-1])
        for line in document.read_text(encoding="utf-8").splitlines()
        if line.startswith("| ") and not line.startswith("| Internal ID ")
    )
    assert (
        tuple(
            (agent.internal_id, agent.alias, agent.professional_role)
            for agent in agents
        )
        == rows
    )
    assert agents == DEFAULT_AGENT_DEFINITIONS
    assert build_default_agent_registry().list_agents() == agents


@pytest.mark.parametrize("identity", CANONICAL_IDS)
def test_every_canonical_lookup_uses_id_not_alias(identity: str) -> None:
    store = build_default_agent_registry()
    found = store.require(identity)
    assert store.get(identity) is found and store.contains(identity)
    assert store.get(f" \t{identity}\n") is found
    assert found.internal_id == identity and found.alias is not None
    assert store.get(found.alias) is None and not store.contains(found.alias)
    with pytest.raises(UnknownAgentError):
        store.require(found.alias)


def test_missing_lookup_and_empty_custom_registry() -> None:
    store = AgentRegistry()
    assert store.list_agents() == ()
    assert store.get("unknown") is None and not store.contains("unknown")
    with pytest.raises(UnknownAgentError):
        store.require("unknown")
    assert build_default_agent_registry().get("STRATEGIST") is None


@pytest.mark.parametrize("method", ["get", "require", "contains"])
@pytest.mark.parametrize("value", ["", " \t\n", None, True, 1, 1.5, b"voice", object()])
def test_invalid_authoritative_lookup_is_rejected(method: str, value: object) -> None:
    store = build_default_agent_registry()
    with pytest.raises(ValidationError):
        getattr(store, method)(value)
    assert tuple(agent.internal_id for agent in store.list_agents()) == CANONICAL_IDS


@pytest.mark.parametrize("field", ["internal_id", "alias", "professional_role"])
@pytest.mark.parametrize(
    "value", ["", " \t\n", True, 1, 1.5, b"text", [], {}, object()]
)
def test_invalid_definition_text_is_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        AgentDefinition.model_validate({**definition().model_dump(), field: value})


@pytest.mark.parametrize("field", ["internal_id", "professional_role"])
def test_required_definition_fields_reject_none_and_omission(field: str) -> None:
    values = definition().model_dump()
    with pytest.raises(ValidationError):
        AgentDefinition.model_validate({**values, field: None})
    del values[field]
    with pytest.raises(ValidationError):
        AgentDefinition.model_validate(values)


def test_whitespace_normalization_and_optional_alias() -> None:
    agent = AgentDefinition(internal_id=" custom ", professional_role=" Role \n")
    assert agent.internal_id == "custom" and agent.professional_role == "Role"
    assert agent.alias is None
    assert definition(alias=" Display ").alias == "Display"


@pytest.mark.parametrize("alias", [None, "New display label"])
def test_all_aliases_removable_or_replaceable_without_identity_changes(
    alias: str | None,
) -> None:
    original = build_default_agent_registry()
    renamed = AgentRegistry(
        agents=tuple(
            AgentDefinition(
                internal_id=agent.internal_id,
                alias=alias,
                professional_role=agent.professional_role,
            )
            for agent in original.list_agents()
        )
    )
    assert tuple(agent.internal_id for agent in renamed.list_agents()) == CANONICAL_IDS
    for identity in CANONICAL_IDS:
        assert renamed.contains(identity)
        assert (
            renamed.require(identity).professional_role
            == original.require(identity).professional_role
        )
        assert renamed.require(identity).alias == alias
    assert all(agent.alias is not None for agent in original.list_agents())


def test_alias_collision_does_not_shadow_authoritative_id() -> None:
    store = AgentRegistry(
        agents=(definition("alpha", "beta"), definition("beta", "alpha"))
    )
    assert store.require("alpha").internal_id == "alpha"
    assert store.require("beta").internal_id == "beta"


def test_duplicate_ids_rejected_after_normalization() -> None:
    with pytest.raises(ValidationError, match="Duplicate agent internal ID"):
        AgentRegistry(agents=(definition(" custom "), definition("custom", None)))


def test_custom_input_is_snapshotted_and_nested_models_revalidated() -> None:
    first = definition("zeta")
    second = definition("alpha", None)
    inputs = [first, second]
    store = AgentRegistry.model_validate({"agents": inputs})
    inputs.clear()
    assert tuple(agent.internal_id for agent in store.list_agents()) == (
        "zeta",
        "alpha",
    )
    assert store.require("zeta") is not first
    first.__dict__["alias"] = "Changed caller metadata"
    assert store.require("zeta").alias == "Display"
    forged = second.model_copy(update={"internal_id": True})
    with pytest.raises(ValidationError):
        AgentRegistry(agents=(forged,))
    external = {"internal_id": "dict", "alias": None, "professional_role": "Role"}
    another = AgentRegistry.model_validate({"agents": [external]})
    external["professional_role"] = "Changed"
    assert another.require("dict").professional_role == "Role"


def test_definitions_registry_and_listing_are_immutable_extra_forbidden() -> None:
    store = build_default_agent_registry()
    for agent in store.list_agents():
        for field in AgentDefinition.model_fields:
            with pytest.raises(ValidationError):
                setattr(agent, field, "Changed")
        with pytest.raises(ValidationError):
            AgentDefinition.model_validate(
                {**agent.model_dump(), "provider": "forbidden"}
            )
    with pytest.raises(ValidationError):
        store.agents = ()
    with pytest.raises(ValidationError):
        AgentRegistry.model_validate({"agents": [], "callback": "forbidden"})
    listed = store.list_agents()
    shortened = listed[:-1]
    assert len(shortened) == 29 and len(store.list_agents()) == 30
    assert not hasattr(listed, "append")
    assert not hasattr(store, "register") and not hasattr(store, "remove")


def test_json_serialization_is_deterministic_and_round_trips() -> None:
    store = build_default_agent_registry()
    encoded = store.model_dump_json()
    assert build_default_agent_registry().model_dump_json() == encoded
    assert AgentRegistry.model_validate_json(encoded) == store
    for agent in store.list_agents():
        assert AgentDefinition.model_validate_json(agent.model_dump_json()) == agent


def test_special_role_boundaries_are_descriptive_and_distinct() -> None:
    store = build_default_agent_registry()
    expected = {
        "phoenix": "Supreme orchestrator",
        "strategist": "Chief strategist/master planner",
        "verifier": "Verification/QA",
        "adaptive_recovery": "Adaptive recovery/self-healing",
        "failsafe": "Emergency degraded fail-safe",
        "perimeter_security": "Security/perimeter",
        "ethics_compliance": "Legal/ethics/compliance",
        "scenario_analysis": "Scenario/trade-off analysis",
        "system_intelligence": "Active system intelligence/monitoring",
        "silent_monitor": "Logs/anomaly monitoring",
        "media": "Spotify/YouTube/media",
        "memory": "Long-term memory",
        "knowledge_rag": "Knowledge indexing/RAG",
    }
    for identity, role in expected.items():
        assert store.require(identity).professional_role == role
    assert [
        a.internal_id
        for a in store.list_agents()
        if a.professional_role == "Supreme orchestrator"
    ] == ["phoenix"]
    assert (
        store.require("strategist").professional_role
        != store.require("scenario_analysis").professional_role
    )
    assert (
        store.require("system_intelligence").professional_role
        != store.require("silent_monitor").professional_role
    )
    assert store.get("Spotify") is None and store.get("YouTube") is None
    # These three fields describe identities only. LEVI does not repair by
    # default; MAHORAGA recovery is bounded and cannot bypass permissions;
    # KAIDO is degraded-only. GOJO/FUJITORA advise, never authorize. Enforcing
    # these rules belongs to later orchestration and deterministic permissions.
    assert set(AgentDefinition.model_fields) == {
        "internal_id",
        "alias",
        "professional_role",
    }
    for agent in store.list_agents():
        serialized = agent.model_dump_json().lower()
        assert all(
            word not in serialized
            for word in (
                "ollama",
                "colibri",
                "http://",
                "https://",
                "qwen",
                "embeddinggemma",
                "permissionengine",
            )
        )


def test_registry_imports_only_data_validation_facilities() -> None:
    tree = ast.parse(inspect.getsource(registry))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module in {"typing", "pydantic"}
        if isinstance(node, ast.Import):
            assert not node.names
        assert not isinstance(node, (ast.AsyncFunctionDef, ast.Await))
    assert not any(
        isinstance(value, AgentRegistry) for value in vars(registry).values()
    )


def test_fresh_interpreter_import_build_read_isolation(tmp_path: Path) -> None:
    script = """
import asyncio, importlib.abc, os, socket, sqlite3, subprocess, sys, threading
from pathlib import Path
def forbidden(*args, **kwargs):
    raise AssertionError("Unexpected agent-registry runtime activity")
def audit(event, args):
    if event == "open" and args[2] & (
        os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    ):
        forbidden()
class BlockImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        roots = ("phoenix.models", "phoenix.core", "phoenix.api", "phoenix.persistence",
                 "phoenix.tools", "phoenix.permissions", "phoenix.memory",
                 "phoenix.voice", "phoenix.recovery", "httpx", "psutil", "alembic")
        if any(fullname == root or fullname.startswith(root + ".") for root in roots):
            forbidden()
sys.dont_write_bytecode = True
sys.meta_path.insert(0, BlockImports())
sys.addaudithook(audit)
os.mkdir = forbidden
socket.socket = forbidden
socket.create_connection = forbidden
sqlite3.connect = forbidden
subprocess.Popen = forbidden
threading.Thread.start = forbidden
asyncio.create_task = forbidden
from phoenix.agents.registry import (
    AgentDefinition, AgentRegistry, build_default_agent_registry
)
custom = AgentRegistry(agents=(
    AgentDefinition(internal_id="custom", professional_role="Role"),
))
assert custom.require("custom").alias is None
store = build_default_agent_registry()
assert len(store.list_agents()) == 30
for agent in store.list_agents():
    assert store.require(agent.internal_id) == agent
    assert store.contains(agent.internal_id)
    assert store.get(agent.alias) is None
assert store.get("missing") is None
assert store.model_dump_json()
assert not list(Path.cwd().iterdir())
print("Agent registry isolation: PASS")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert result.stdout.strip() == "Agent registry isolation: PASS"
