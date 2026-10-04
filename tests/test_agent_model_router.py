"""PHX-023 deterministic routing, contract boundaries, and isolation evidence."""

import ast
import inspect
import json
import subprocess
import sys
from pathlib import Path
from types import MappingProxyType
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError

from phoenix.agents import router
from phoenix.agents.registry import AgentDefinition, AgentRegistry
from phoenix.agents.router import (
    DOMAIN_AGENT_IDS,
    AgentModelRouter,
    AgentModelRoutingError,
    MissingRouteAgentError,
    ModelRouteCandidate,
    NoEligibleModelError,
    PermissionRisk,
    ProviderRouteHealth,
    RouteDecision,
    RoutingDomain,
    RoutingRequest,
)
from phoenix.models.provider import ModelCapabilities, ProviderHealthStatus
from phoenix.models.registry import (
    CapabilityRequirements,
    ModelCapabilityRecord,
    ModelCapabilityRegistry,
)

EXPECTED = {
    "intelligence": "intelligence",
    "automation": "automation",
    "mission_coordination": "mission_coordinator",
    "project_management": "project_manager",
    "build_engineering": "build_engineer",
    "documents": "document_analyst",
    "system_intelligence": "system_intelligence",
    "diagnostics": "diagnostics",
    "security": "perimeter_security",
    "health": "health_biometrics",
    "compliance": "ethics_compliance",
    "media": "media",
    "infrastructure": "infrastructure",
    "knowledge_rag": "knowledge_rag",
    "optimization": "background_optimizer",
    "scenario_analysis": "scenario_analysis",
    "heavy_compute": "heavy_compute",
    "threat_recon": "threat_recon",
    "priority_execution": "priority_executor",
    "user_interaction": "user_interaction",
    "silent_monitoring": "silent_monitor",
    "memory": "memory",
    "web_research": "web_research",
    "voice": "voice",
    "computer_control": "computer_control",
}


def caps(**changes: object) -> ModelCapabilities:
    return ModelCapabilities.model_validate(
        {
            "provider_id": "fixture",
            "model_id": "model",
            "supports_streaming": False,
            "supports_structured_output": False,
            "supports_tool_use": False,
            "supports_vision": False,
            "supports_cancellation": False,
            "context_limit_tokens": None,
            **changes,
        }
    )


def candidate(**changes: object) -> ModelRouteCandidate:
    return ModelRouteCandidate.model_validate(
        {
            "provider_id": "fixture",
            "model_id": "model",
            "resource_eligible": True,
            **changes,
        }
    )


def health(**changes: object) -> ProviderRouteHealth:
    return ProviderRouteHealth.model_validate(
        {"provider_id": "fixture", "status": "healthy", **changes}
    )


def request(**changes: object) -> RoutingRequest:
    return RoutingRequest.model_validate(
        {
            "route_id": "route",
            "domain": "documents",
            "model_candidates": (candidate(),),
            "provider_health": (health(),),
            "requirements": CapabilityRequirements(),
            "permission_risk": "P0",
            **changes,
        }
    )


def owner(*snapshots: ModelCapabilities) -> AgentModelRouter:
    store = ModelCapabilityRegistry()
    for snapshot in snapshots or (caps(),):
        store.register(snapshot)
    return AgentModelRouter(capability_registry=store)


def test_default_construction_and_exact_frozen_map() -> None:
    assert {
        domain.value: identity for domain, identity in DOMAIN_AGENT_IDS.items()
    } == (EXPECTED)
    assert [domain.value for domain in RoutingDomain] == list(EXPECTED)
    assert isinstance(DOMAIN_AGENT_IDS, MappingProxyType)
    with pytest.raises(TypeError):
        DOMAIN_AGENT_IDS[RoutingDomain.DOCUMENTS] = "phoenix"  # type: ignore[index]
    with pytest.raises(NoEligibleModelError):
        AgentModelRouter().route(request())


@pytest.mark.parametrize("domain,identity", EXPECTED.items())
@pytest.mark.parametrize("alias", [None, "changed display alias"])
def test_all_domains_ignore_alias_and_role_text(
    domain: str, identity: str, alias: str | None
) -> None:
    store = ModelCapabilityRegistry()
    store.register(caps())
    agents = AgentRegistry(
        agents=(
            AgentDefinition(
                internal_id=identity,
                alias=alias,
                professional_role="arbitrary display role",
            ),
        )
    )
    result = AgentModelRouter(agents, store).route(request(domain=domain))
    assert result.agent_id == identity and result.domain.value == domain
    assert result.provider_id == "fixture" and result.model_id == "model"


@pytest.mark.parametrize(
    "invalid",
    [
        "phoenix",
        "strategist",
        "verifier",
        "adaptive_recovery",
        "failsafe",
        "PHOENIX",
        "AIZEN",
        "LEVI",
        "MAHORAGA",
        "KAIDO",
        "ROBIN",
        "ARMIN",
        "DOCUMENTS",
        "unknown",
        "",
        " documents ",
        None,
        1,
    ],
)
def test_control_id_alias_or_unknown_domain_rejected(invalid: object) -> None:
    with pytest.raises(ValidationError):
        request(domain=invalid)


@pytest.mark.parametrize(
    "identity",
    ["phoenix", "strategist", "verifier", "adaptive_recovery", "failsafe", "ROBIN"],
)
def test_missing_agent_cannot_fall_back_to_alias_or_control(identity: str) -> None:
    agents = AgentRegistry(
        agents=(
            AgentDefinition(
                internal_id=identity,
                alias="document_analyst",
                professional_role="Documents",
            ),
        )
    )
    with pytest.raises(MissingRouteAgentError, match="Mapped route agent is absent"):
        AgentModelRouter(agents).route(request())


def test_ids_roundtrip_frozen_contracts_and_input_snapshots() -> None:
    candidates = [candidate(provider_id=" fixture ", model_id=" model ")]
    healths = [health(provider_id=" fixture ")]
    req = request(
        route_id=" route ", model_candidates=candidates, provider_health=healths
    )
    candidates.clear()
    healths.clear()
    result = owner().route(req)
    assert req.route_id == result.route_id == "route"
    assert req.model_candidates == (candidate(),) and req.provider_health == (health(),)
    payload = req.model_dump()
    del payload["route_id"]
    first = RoutingRequest.model_validate(payload)
    second = RoutingRequest.model_validate(payload)
    UUID(first.route_id)
    assert first.route_id != second.route_id
    assert RoutingRequest.model_validate_json(req.model_dump_json()) == req
    assert RouteDecision.model_validate_json(result.model_dump_json()) == result
    for model in (
        req,
        result,
        candidate(),
        health(),
        req.requirements,
        result.capabilities,
    ):
        field = next(iter(type(model).model_fields))
        with pytest.raises(ValidationError, match="frozen"):
            setattr(model, field, "changed")
    assert result.requirements is not req.requirements


@pytest.mark.parametrize("value", ["", " \t ", None, 7, True, b"id"])
@pytest.mark.parametrize(
    "contract,field",
    [
        ("request", "route_id"),
        ("candidate", "provider_id"),
        ("candidate", "model_id"),
        ("health", "provider_id"),
        ("decision", "route_id"),
        ("decision", "agent_id"),
        ("decision", "provider_id"),
        ("decision", "model_id"),
    ],
)
def test_invalid_ids_rejected(contract: str, field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        if contract == "request":
            request(**{field: value})
        elif contract == "candidate":
            candidate(**{field: value})
        elif contract == "health":
            health(**{field: value})
        else:
            data = owner().route(request()).model_dump()
            data[field] = value
            RouteDecision.model_validate(data)


@pytest.mark.parametrize("value", [0, 1, "true", "false", None, []])
def test_resource_fact_is_strict_bool(value: object) -> None:
    with pytest.raises(ValidationError):
        candidate(resource_eligible=value)


@pytest.mark.parametrize("value", [None, "unknown", "HEALTHY", "", True, 1])
def test_health_status_is_explicit(value: object) -> None:
    with pytest.raises(ValidationError):
        health(status=value)


@pytest.mark.parametrize("value", [None, "p0", "P6", "approved", True, 1])
def test_risk_is_explicit(value: object) -> None:
    with pytest.raises(ValidationError):
        request(permission_risk=value)


@pytest.mark.parametrize("risk", list(PermissionRisk))
def test_risk_metadata_preserved_without_authority(risk: PermissionRisk) -> None:
    result = owner(caps(supports_tool_use=True)).route(
        request(
            permission_risk=risk,
            requirements=CapabilityRequirements(requires_tool_use=True),
        )
    )
    assert result.permission_risk == risk
    assert result.capabilities.supports_tool_use
    assert set(result.model_dump()) == {
        "route_id",
        "domain",
        "agent_id",
        "provider_id",
        "model_id",
        "requirements",
        "capabilities",
        "permission_risk",
    }


@pytest.mark.parametrize("contract", ["request", "candidate", "health", "decision"])
@pytest.mark.parametrize(
    "field",
    [
        "alias",
        "client",
        "benchmark_score",
        "approved",
        "task_state",
        "callback",
        "prompt",
    ],
)
def test_extra_fields_rejected(contract: str, field: str) -> None:
    models: dict[str, BaseModel] = {
        "request": request(),
        "candidate": candidate(),
        "health": health(),
        "decision": owner().route(request()),
    }
    model = models[contract]
    with pytest.raises(ValidationError, match="Extra inputs"):
        type(model).model_validate({**model.model_dump(), field: "unexpected"})


@pytest.mark.parametrize("value", [(), [], None])
def test_candidate_required(value: object) -> None:
    with pytest.raises(ValidationError):
        request(model_candidates=value)


@pytest.mark.parametrize("eligible", [True, False])
def test_duplicate_normalized_pair_rejected(eligible: bool) -> None:
    with pytest.raises(ValidationError, match="Duplicate model candidate"):
        request(
            model_candidates=(
                candidate(),
                candidate(
                    provider_id=" fixture ",
                    model_id=" model ",
                    resource_eligible=eligible,
                ),
            )
        )


@pytest.mark.parametrize("status", list(ProviderHealthStatus))
def test_duplicate_normalized_provider_health_rejected(
    status: ProviderHealthStatus,
) -> None:
    with pytest.raises(ValidationError, match="Duplicate provider health"):
        request(
            provider_health=(health(), health(provider_id=" fixture ", status=status))
        )


@pytest.mark.parametrize(
    "failure",
    [
        "resource",
        "degraded",
        "unavailable",
        "missing_health",
        "missing_record",
        "capability",
    ],
)
def test_ineligible_first_candidate_skipped_and_all_ineligible_fails(
    failure: str,
) -> None:
    first = candidate(resource_eligible=failure != "resource")
    first_health = health(
        status=failure if failure in {"degraded", "unavailable"} else "healthy"
    )
    store = ModelCapabilityRegistry()
    if failure != "missing_record":
        store.register(caps(supports_vision=failure != "capability"))
    store.register(caps(provider_id="second", supports_vision=True))
    route = AgentModelRouter(capability_registry=store)
    req = request(
        model_candidates=(first, candidate(provider_id="second")),
        provider_health=((first_health,) if failure != "missing_health" else ())
        + (health(provider_id="second"),),
        requirements=CapabilityRequirements(requires_vision=True),
    )
    before = store.list_records()
    assert route.route(req).provider_id == "second"
    assert store.list_records() == before
    invalid = req.model_copy(update={"model_candidates": (first,)})
    with pytest.raises(NoEligibleModelError, match="No eligible model candidate"):
        route.route(invalid)
    assert store.list_records() == before
    assert issubclass(NoEligibleModelError, AgentModelRoutingError)


@pytest.mark.parametrize(
    "requirement,support",
    [
        ("requires_streaming", "supports_streaming"),
        ("requires_structured_output", "supports_structured_output"),
        ("requires_tool_use", "supports_tool_use"),
        ("requires_vision", "supports_vision"),
        ("requires_cancellation", "supports_cancellation"),
    ],
)
def test_each_technical_requirement_uses_registry_rules(
    requirement: str,
    support: str,
) -> None:
    req = request(
        model_candidates=(candidate(), candidate(model_id="supported")),
        requirements=CapabilityRequirements.model_validate({requirement: True}),
    )
    route = owner(caps(), caps(model_id="supported", **{support: True}))
    assert route.route(req).model_id == "supported"
    assert route.route(request()).model_id == "model"  # False is no constraint.


@pytest.mark.parametrize(
    "context,eligible", [(None, False), (31, False), (32, True), (33, True)]
)
def test_minimum_context_unknown_small_and_exact_boundary(
    context: int | None,
    eligible: bool,
) -> None:
    route = owner(caps(context_limit_tokens=context))
    req = request(requirements=CapabilityRequirements(min_context_tokens=32))
    if eligible:
        assert route.route(req).model_id == "model"
    else:
        with pytest.raises(NoEligibleModelError):
            route.route(req)
    assert route.route(request()).model_id == "model"


def test_requirements_combined_and_registry_matching_reused() -> None:
    class ObservedRegistry(ModelCapabilityRegistry):
        calls = 0

        def matching(
            self, requirements: CapabilityRequirements
        ) -> tuple[ModelCapabilityRecord, ...]:
            self.calls += 1
            return super().matching(requirements)

    # Match through the existing public method, without refreshing or re-ranking.
    store = ObservedRegistry()
    requirements = CapabilityRequirements(
        requires_streaming=True,
        requires_structured_output=True,
        requires_tool_use=True,
        requires_vision=True,
        requires_cancellation=True,
        min_context_tokens=32,
    )
    full = caps(
        supports_streaming=True,
        supports_structured_output=True,
        supports_tool_use=True,
        supports_vision=True,
        supports_cancellation=True,
        context_limit_tokens=32,
    )
    store.register(full)
    result = AgentModelRouter(capability_registry=store).route(
        request(requirements=requirements)
    )
    assert store.calls == 1
    assert store.matching(requirements)[0].capabilities == result.capabilities == full


@pytest.mark.parametrize(
    "providers",
    [("z-provider", "a-provider"), ("ollama", "colibri"), ("colibri", "ollama")],
)
def test_caller_order_wins_without_provider_or_alphabetical_preference(
    providers: tuple[str, str],
) -> None:
    first, second = providers
    route = owner(
        caps(provider_id=first, model_id="z-model"),
        caps(provider_id=second, model_id="a-model"),
    )
    req = request(
        model_candidates=(
            candidate(provider_id=first, model_id="z-model"),
            candidate(provider_id=second, model_id="a-model"),
        ),
        provider_health=(health(provider_id=first), health(provider_id=second)),
    )
    assert route.route(req).provider_id == first
    assert (
        route.route(
            req.model_copy(
                update={"model_candidates": tuple(reversed(req.model_candidates))}
            )
        ).provider_id
        == second
    )


def test_exact_pair_identity_and_later_explicit_registration() -> None:
    store = ModelCapabilityRegistry()
    store.register(caps(provider_id="other"))
    store.register(caps(model_id="other"))
    route = AgentModelRouter(capability_registry=store)
    with pytest.raises(NoEligibleModelError):
        route.route(request())
    stored = store.register(caps())
    result = route.route(request())
    assert result.capabilities is not stored.capabilities
    assert route.route(request()) == result
    store.remove("fixture", "model")
    with pytest.raises(NoEligibleModelError):
        route.route(request())
    store.register(caps(supports_vision=True))
    assert not result.capabilities.supports_vision
    assert route.route(request()).capabilities.supports_vision


@pytest.mark.parametrize(
    "changes",
    [
        {"agent_id": "ROBIN"},
        {"agent_id": "phoenix"},
        {"agent_id": "memory"},
        {"provider_id": "other"},
        {"model_id": "other"},
        {"domain": "memory"},
    ],
)
def test_result_identity_contradictions_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="contradicts"):
        RouteDecision.model_validate(
            {**owner().route(request()).model_dump(), **changes}
        )


def test_forged_or_extended_neutral_inputs_revalidated() -> None:
    with pytest.raises(ValidationError):
        owner().route(request().model_copy(update={"permission_risk": "approved"}))
    with pytest.raises(ValidationError):
        request(
            model_candidates=(candidate().model_copy(update={"resource_eligible": 1}),)
        )
    with pytest.raises(ValidationError):
        request(provider_health=(health().model_copy(update={"status": "unknown"}),))
    with pytest.raises(ValidationError):
        request(requirements=CapabilityRequirements.model_construct(requires_vision=1))

    class ExtendedCandidate(ModelRouteCandidate):
        callback: str = "unexpected"

    class ExtendedRequirements(CapabilityRequirements):
        score: int = 42

    class ExtendedCapabilities(ModelCapabilities):
        client: str = "unexpected"

    with pytest.raises(ValidationError, match="Extra inputs"):
        request(model_candidates=(ExtendedCandidate(**candidate().model_dump()),))
    with pytest.raises(ValidationError, match="Extra inputs"):
        request(requirements=ExtendedRequirements())
    data = owner().route(request()).model_dump()
    data["capabilities"] = ExtendedCapabilities(**caps().model_dump())
    with pytest.raises(ValidationError, match="Extra inputs"):
        RouteDecision.model_validate(data)
    forged = AgentRegistry.model_construct(
        agents=(
            AgentDefinition.model_construct(
                internal_id="document_analyst", alias=None, professional_role=7
            ),
        )
    )
    with pytest.raises(ValidationError):
        AgentModelRouter(forged)


def test_production_imports_calls_and_fields_have_only_routing_scope() -> None:
    source = inspect.getsource(router)
    allowed = {
        "collections.abc",
        "enum",
        "types",
        "typing",
        "uuid",
        "pydantic",
        "phoenix.agents.registry",
        "phoenix.models.provider",
        "phoenix.models.registry",
    }
    forbidden_calls = {
        "health",
        "capabilities",
        "generate",
        "stream",
        "cancel",
        "refresh",
        "acquire",
        "execute",
        "plan",
        "emit",
        "publish",
        "open",
        "create_task",
        "create_connection",
        "Popen",
    }
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            assert node.module in allowed
        if isinstance(node, ast.Import):
            assert all(alias.name in allowed for alias in node.names)
        assert not isinstance(node, (ast.AsyncFunctionDef, ast.Await))
        if isinstance(node, ast.Call):
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else (node.func.id if isinstance(node.func, ast.Name) else "")
            )
            assert name not in forbidden_calls
    assert not inspect.iscoroutinefunction(AgentModelRouter.route)
    for text in ("qwen", "embeddinggemma", "ollama", "colibri", "benchmark_score"):
        assert text not in source.lower()
    assert set(ModelRouteCandidate.model_fields) == {
        "provider_id",
        "model_id",
        "resource_eligible",
    }
    assert set(ProviderRouteHealth.model_fields) == {"provider_id", "status"}
    assert set(RoutingRequest.model_fields) == {
        "route_id",
        "domain",
        "model_candidates",
        "provider_health",
        "requirements",
        "permission_risk",
    }
    assert set(DOMAIN_AGENT_IDS.values()).isdisjoint(
        {"phoenix", "strategist", "verifier", "adaptive_recovery", "failsafe"}
    )


def test_fresh_interpreter_import_construct_and_route_isolation(tmp_path: Path) -> None:
    script = """
import asyncio, importlib.abc, os, socket, sqlite3, subprocess, sys, threading
from pathlib import Path
def forbidden(*args, **kwargs):
    raise AssertionError("Unexpected router runtime activity")
def audit(event, args):
    if event == "open" and args[2] & (
        os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    ):
        forbidden()
class BlockImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        roots = ("phoenix.models.ollama", "phoenix.models.colibri",
                 "phoenix.models.embedding", "phoenix.models.lifecycle",
                 "phoenix.models.benchmark", "phoenix.core", "phoenix.api",
                 "phoenix.persistence", "phoenix.tools", "phoenix.permissions",
                 "phoenix.memory", "phoenix.voice", "phoenix.recovery",
                 "phoenix.agents.orchestrator", "phoenix.agents.planner",
                 "httpx", "psutil", "alembic")
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
asyncio.BaseEventLoop.create_task = forbidden
from phoenix.agents.router import (
    AgentModelRouter, ModelRouteCandidate, ProviderRouteHealth, RoutingRequest,
    RoutingDomain, PermissionRisk, RouteDecision, NoEligibleModelError
)
from phoenix.models.provider import ModelCapabilities
from phoenix.models.registry import CapabilityRequirements, ModelCapabilityRegistry
empty = AgentModelRouter()
store = ModelCapabilityRegistry()
store.register(ModelCapabilities(
    provider_id="fixture", model_id="model", supports_streaming=False,
    supports_structured_output=False, supports_tool_use=False, supports_vision=False,
    supports_cancellation=False
))
request = RoutingRequest(
    domain=RoutingDomain.DOCUMENTS,
    model_candidates=[ModelRouteCandidate(
        provider_id="fixture", model_id="model", resource_eligible=True)],
    provider_health=[ProviderRouteHealth(provider_id="fixture", status="healthy")],
    requirements=CapabilityRequirements(), permission_risk=PermissionRisk.P5
)
route = AgentModelRouter(capability_registry=store)
result = route.route(request)
assert result.agent_id == "document_analyst" and result.permission_risk == "P5"
assert route.route(request) == result
assert RouteDecision.model_validate_json(result.model_dump_json()) == result
try:
    empty.route(request)
except NoEligibleModelError:
    pass
else:
    raise AssertionError("Unexpected empty-registry fallback")
assert not list(Path.cwd().iterdir())
print("Router isolation: PASS")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert result.stdout.strip() == "Router isolation: PASS"
    assert list(tmp_path.iterdir()) == []
    assert json.loads(owner().route(request()).model_dump_json())["agent_id"] == (
        "document_analyst"
    )
