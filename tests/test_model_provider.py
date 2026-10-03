"""PHX-010 contract validation, substitutability, and construction isolation."""

import ast
import asyncio
import inspect
import json
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError

from phoenix.models import provider
from phoenix.models.provider import (
    CancellationResult,
    CancellationStatus,
    GenerationChunk,
    GenerationMetrics,
    GenerationRequest,
    GenerationResponse,
    MessageRole,
    ModelCapabilities,
    ModelMessage,
    ModelProvider,
    ModelProviderError,
    ProviderHealth,
    ProviderHealthStatus,
    ProviderUnavailableError,
    TokenUsage,
)


class FakeProvider(ModelProvider):
    """Test-only deterministic backend; explicit in-memory operations only."""

    def __init__(self, provider_id: str = "fixture") -> None:
        super().__init__(provider_id)
        self.operations: set[str] = set()

    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider_id=self.provider_id, status=ProviderHealthStatus.HEALTHY
        )

    async def capabilities(self, model_id: str) -> ModelCapabilities:
        return ModelCapabilities(
            provider_id=self.provider_id,
            model_id=model_id,
            supports_streaming=True,
            supports_structured_output=False,
            supports_tool_use=False,
            supports_vision=False,
            supports_cancellation=True,
            context_limit_tokens=16384,
        )

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.operations.add(request.operation_id)
        return GenerationResponse(
            operation_id=request.operation_id,
            provider_id=self.provider_id,
            model_id=request.model_id,
            text="".join(message.text for message in request.messages),
            finish_reason="stop",
        )

    async def stream(
        self, request: GenerationRequest
    ) -> AsyncIterator[GenerationChunk]:
        self.operations.add(request.operation_id)
        for message in request.messages:
            yield GenerationChunk(
                operation_id=request.operation_id,
                provider_id=self.provider_id,
                model_id=request.model_id,
                text=message.text,
            )
        yield GenerationChunk(
            operation_id=request.operation_id,
            provider_id=self.provider_id,
            model_id=request.model_id,
            text="",
            is_final=True,
            finish_reason="stop",
            usage=TokenUsage(output_tokens=2),
            metrics=GenerationMetrics(duration_ms=0),
        )

    async def cancel(self, operation_id: str) -> CancellationResult:
        status = CancellationStatus.NOT_FOUND
        if operation_id in self.operations:
            self.operations.remove(operation_id)
            status = CancellationStatus.ACCEPTED
        return CancellationResult(
            operation_id=operation_id, provider_id=self.provider_id, status=status
        )


class UnavailableProvider(FakeProvider):
    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider_id=self.provider_id, status=ProviderHealthStatus.UNAVAILABLE
        )

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        raise ProviderUnavailableError("Test provider unavailable")

    async def cancel(self, operation_id: str) -> CancellationResult:
        return CancellationResult(
            operation_id=operation_id,
            provider_id=self.provider_id,
            status=CancellationStatus.UNSUPPORTED,
        )


@pytest.fixture
def generation_request() -> GenerationRequest:
    return GenerationRequest(
        model_id="neutral-model",
        messages=(
            ModelMessage(role=MessageRole.USER, text="first"),
            ModelMessage(role=MessageRole.TOOL, text="second"),
        ),
    )


def test_abstract_contract_cannot_instantiate() -> None:
    with pytest.raises(TypeError):
        ModelProvider("fixture")  # type: ignore[abstract]

    class IncompleteProvider(ModelProvider):
        async def health(self) -> ProviderHealth:
            return ProviderHealth(
                provider_id=self.provider_id, status=ProviderHealthStatus.HEALTHY
            )

    with pytest.raises(TypeError):
        IncompleteProvider("fixture")  # type: ignore[abstract]
    assert ModelProvider.__abstractmethods__ == {
        "health",
        "capabilities",
        "generate",
        "stream",
        "cancel",
    }


@pytest.mark.parametrize("identity", ["", "   ", 1])
def test_provider_id_rejected(identity: object) -> None:
    with pytest.raises(ValidationError):
        FakeProvider(identity)  # type: ignore[arg-type]


def test_fake_provider_substitutability(generation_request: GenerationRequest) -> None:
    request = generation_request

    async def run(backend: ModelProvider) -> None:
        identity = backend.provider_id
        health = await backend.health()
        assert (
            health.provider_id == identity
            and health.status is ProviderHealthStatus.HEALTHY
        )
        caps = await backend.capabilities(request.model_id)
        assert caps.provider_id == identity and caps.model_id == request.model_id
        assert caps.supports_streaming and caps.supports_cancellation
        response = await backend.generate(request)
        assert response.operation_id == request.operation_id
        assert (
            response.provider_id == identity and response.model_id == request.model_id
        )
        assert response.text == "firstsecond" and response.usage is None
        chunks = [chunk async for chunk in backend.stream(request)]
        assert all(isinstance(chunk, GenerationChunk) for chunk in chunks)
        assert [chunk.text for chunk in chunks] == ["first", "second", ""]
        assert all(chunk.operation_id == request.operation_id for chunk in chunks)
        assert all(chunk.provider_id == identity for chunk in chunks)
        assert all(chunk.model_id == request.model_id for chunk in chunks)
        assert not any(chunk.is_final for chunk in chunks[:-1])
        assert chunks[-1].is_final and chunks[-1].finish_reason == "stop"
        assert chunks[-1].usage == TokenUsage(output_tokens=2)
        assert chunks[-1].metrics == GenerationMetrics(duration_ms=0)
        assert backend.provider_id == identity

    for identity in ("fixture-one", "fixture-two"):
        asyncio.run(run(FakeProvider(identity)))


def test_cancellation_scope_and_outcomes(generation_request: GenerationRequest) -> None:
    request = generation_request

    async def run() -> None:
        backend = FakeProvider()
        other = request.model_copy(update={"operation_id": "other-operation"})
        await backend.generate(request)
        await backend.generate(other)
        result = await backend.cancel(request.operation_id)
        assert result.status is CancellationStatus.ACCEPTED
        assert result.operation_id == request.operation_id
        assert result.provider_id == backend.provider_id
        assert backend.operations == {other.operation_id}
        assert (
            await backend.cancel(request.operation_id)
        ).status is CancellationStatus.NOT_FOUND
        assert (
            await backend.cancel(other.operation_id)
        ).status is CancellationStatus.ACCEPTED
        unsupported: ModelProvider = UnavailableProvider()
        result = await unsupported.cancel(request.operation_id)
        assert result.status is CancellationStatus.UNSUPPORTED
        assert result.operation_id == request.operation_id

    asyncio.run(run())


def test_unavailable_is_explicit_failure(generation_request: GenerationRequest) -> None:
    request = generation_request

    async def run(backend: ModelProvider) -> None:
        assert (await backend.health()).status is ProviderHealthStatus.UNAVAILABLE
        with pytest.raises(ProviderUnavailableError) as failure:
            await backend.generate(request)
        assert isinstance(failure.value, ModelProviderError)

    asyncio.run(run(UnavailableProvider()))


def test_request_ids_order_and_input_snapshot(
    generation_request: GenerationRequest,
) -> None:
    request = generation_request
    UUID(request.operation_id)
    assert (
        request.operation_id
        != GenerationRequest(model_id="model", messages=()).operation_id
    )
    original = request.model_dump()
    original["operation_id"] = "caller-operation"
    supplied = GenerationRequest.model_validate(original)
    assert supplied.operation_id == "caller-operation"
    messages = [ModelMessage(role=role, text=role.value) for role in MessageRole]
    stops = ["stop"]
    data = GenerationRequest.model_validate(
        {"model_id": "model", "messages": messages, "stop_sequences": stops}
    )
    messages.reverse()
    stops.append("later")
    assert tuple(message.role for message in data.messages) == tuple(MessageRole)
    assert data.stop_sequences == ("stop",)
    with pytest.raises(ValidationError):
        data.messages[0].text = "mutated"
    with pytest.raises(ValidationError):
        data.model_id = "mutated"


@pytest.mark.parametrize("status", list(ProviderHealthStatus))
def test_health_state_and_utc(status: ProviderHealthStatus) -> None:
    health = ProviderHealth(provider_id="fixture", status=status)
    assert health.checked_at.tzinfo is UTC
    stamp = datetime(2026, 10, 4, 1, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    normalized = ProviderHealth(provider_id="fixture", status=status, checked_at=stamp)
    assert normalized.checked_at == stamp and normalized.checked_at.tzinfo is UTC


@pytest.mark.parametrize(
    ("model", "data"),
    [
        (GenerationRequest, {"model_id": "", "messages": []}),
        (GenerationRequest, {"model_id": "   ", "messages": []}),
        (GenerationRequest, {"model_id": "model", "messages": [], "operation_id": ""}),
        (GenerationRequest, {"model_id": "model", "messages": [], "temperature": -1}),
        (
            GenerationRequest,
            {"model_id": "model", "messages": [], "temperature": float("nan")},
        ),
        (
            GenerationRequest,
            {"model_id": "model", "messages": [], "max_output_tokens": 0},
        ),
        (
            GenerationRequest,
            {"model_id": "model", "messages": [], "max_output_tokens": -1},
        ),
        (
            GenerationRequest,
            {"model_id": "model", "messages": [], "max_output_tokens": True},
        ),
        (
            GenerationRequest,
            {"model_id": "model", "messages": [], "stop_sequences": [""]},
        ),
        (
            GenerationRequest,
            {"model_id": "model", "messages": [], "stop_sequences": [" "]},
        ),
        (ModelMessage, {"role": "invalid", "text": "content"}),
        (ModelMessage, {"role": "user", "text": object()}),
        (ProviderHealth, {"provider_id": "fixture", "status": "ready"}),
        (
            ProviderHealth,
            {
                "provider_id": "fixture",
                "status": "healthy",
                "checked_at": datetime(2026, 1, 1),
            },
        ),
        (
            GenerationResponse,
            {"operation_id": "op", "provider_id": "", "model_id": "model", "text": ""},
        ),
        (
            GenerationResponse,
            {
                "operation_id": "op",
                "provider_id": "fixture",
                "model_id": "model",
                "text": "",
                "metadata": object(),
            },
        ),
        (
            GenerationChunk,
            {
                "operation_id": "op",
                "provider_id": "fixture",
                "model_id": "model",
                "text": "",
                "is_final": "yes",
            },
        ),
        (
            CancellationResult,
            {"operation_id": "", "provider_id": "fixture", "status": "accepted"},
        ),
        (
            CancellationResult,
            {"operation_id": "op", "provider_id": "fixture", "status": "success"},
        ),
    ],
)
def test_invalid_contract_data(model: type[BaseModel], data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(data)


@pytest.mark.parametrize("field", ["input_tokens", "output_tokens", "total_tokens"])
@pytest.mark.parametrize("value", [-1, 1.5, True])
def test_invalid_usage(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        TokenUsage.model_validate({field: value})


@pytest.mark.parametrize("field", ["ttft_ms", "duration_ms", "tokens_per_second"])
@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), -float("inf")])
def test_invalid_metrics(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        GenerationMetrics.model_validate({field: value})


@pytest.mark.parametrize("value", [None, 16384, 0, -1, True, "16384"])
def test_capabilities_validation(value: object) -> None:
    data = {
        "provider_id": "fixture",
        "model_id": "model",
        "supports_streaming": True,
        "supports_structured_output": True,
        "supports_tool_use": True,
        "supports_vision": False,
        "supports_cancellation": False,
        "context_limit_tokens": value,
    }
    if value is None or type(value) is int and value > 0:
        assert ModelCapabilities.model_validate(data).context_limit_tokens == value
    else:
        with pytest.raises(ValidationError):
            ModelCapabilities.model_validate(data)
    data["context_limit_tokens"] = None
    data["supports_tool_use"] = "yes"
    with pytest.raises(ValidationError):
        ModelCapabilities.model_validate(data)


def test_unknown_counts_and_metrics_are_not_inferred() -> None:
    assert TokenUsage().model_dump() == {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
    }
    assert TokenUsage(input_tokens=1, output_tokens=2).total_tokens is None
    assert TokenUsage(input_tokens=0).input_tokens == 0
    assert all(value is None for value in GenerationMetrics().model_dump().values())


def test_all_models_frozen_json_round_trip(
    generation_request: GenerationRequest,
) -> None:
    request = generation_request
    caps = asyncio.run(FakeProvider().capabilities(request.model_id))
    response = asyncio.run(FakeProvider().generate(request))
    models = [
        request.messages[0],
        request,
        caps,
        ProviderHealth(provider_id="fixture", status=ProviderHealthStatus.HEALTHY),
        TokenUsage(input_tokens=0),
        GenerationMetrics(ttft_ms=0),
        response,
        GenerationChunk(**response.model_dump(), is_final=True),
        CancellationResult(
            operation_id="op", provider_id="fixture", status=CancellationStatus.ACCEPTED
        ),
    ]
    for model in models:
        encoded = model.model_dump_json()
        assert encoded == model.model_dump_json()
        assert type(model).model_validate_json(encoded) == model
        assert isinstance(json.loads(encoded), dict)
        field = next(iter(type(model).model_fields))
        with pytest.raises(ValidationError):
            setattr(model, field, getattr(model, field))


def test_contract_imports_only_standard_library_and_pydantic() -> None:
    source = inspect.getsource(provider)
    tree = ast.parse(source)
    allowed = {"abc", "collections", "datetime", "enum", "typing", "uuid", "pydantic"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name.split(".")[0] in allowed for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0 and node.module
            assert node.module.split(".")[0] in allowed


def test_import_and_construction_isolation(tmp_path: Path) -> None:
    script = (
        """
import asyncio
from collections.abc import AsyncIterator
import importlib.abc
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import threading

def forbidden(*args, **kwargs):
    raise AssertionError("Unexpected I/O or runtime initialization")

def audit(event, args):
    if event == "open" and args[2] & (
        os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    ):
        forbidden()

class BlockImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        roots = ("fastapi", "starlette", "sqlalchemy", "alembic", "tauri", "react",
                 "ollama", "colibri", "airllm", "httpx", "requests", "psutil",
                 "phoenix.core", "phoenix.api", "phoenix.persistence",
                 "phoenix.agents", "phoenix.tools", "phoenix.permissions",
                 "phoenix.voice")
        if any(fullname == root or fullname.startswith(root + ".") for root in roots):
            forbidden()

sys.dont_write_bytecode = True
sys.addaudithook(audit)
sys.meta_path.insert(0, BlockImports())
os.mkdir = forbidden
sqlite3.connect = forbidden
sqlite3.dbapi2.connect = forbidden
socket.socket = forbidden
socket.create_connection = forbidden
subprocess.Popen = forbidden
threading.Thread.start = forbidden
asyncio.create_task = forbidden
asyncio.BaseEventLoop.create_task = forbidden

from phoenix.models.provider import (
    CancellationResult, CancellationStatus, GenerationChunk, GenerationMetrics,
    GenerationRequest, GenerationResponse, MessageRole, ModelCapabilities,
    ModelMessage, ModelProvider, ProviderHealth, ProviderHealthStatus, TokenUsage,
)
"""
        + inspect.getsource(FakeProvider)
        + """
fake = FakeProvider()
message = ModelMessage(role=MessageRole.TOOL, text="untrusted content only")
request = GenerationRequest(model_id="model", messages=(message,))
ModelCapabilities(provider_id=fake.provider_id, model_id="model",
                  supports_streaming=True, supports_structured_output=True,
                  supports_tool_use=True, supports_vision=True,
                  supports_cancellation=True)
ProviderHealth(provider_id=fake.provider_id, status=ProviderHealthStatus.HEALTHY)
usage = TokenUsage()
metrics = GenerationMetrics()
GenerationResponse(operation_id=request.operation_id, provider_id=fake.provider_id,
                   model_id=request.model_id, text="", usage=usage, metrics=metrics)
GenerationChunk(operation_id=request.operation_id, provider_id=fake.provider_id,
                model_id=request.model_id, text="", is_final=True)
CancellationResult(operation_id=request.operation_id, provider_id=fake.provider_id,
                   status=CancellationStatus.ACCEPTED)
assert not list(Path.cwd().iterdir())
print("Provider import/model/fake construction isolation: PASS")
"""
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert (
        result.stdout.strip()
        == "Provider import/model/fake construction isolation: PASS"
    )
