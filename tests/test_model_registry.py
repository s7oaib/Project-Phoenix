"""PHX-012 neutral snapshot, filtering, atomic refresh, and isolation evidence."""

import ast
import asyncio
import inspect
import json
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from pydantic import ValidationError

from phoenix.models import registry
from phoenix.models.ollama import OllamaProvider
from phoenix.models.provider import (
    CancellationResult,
    GenerationChunk,
    GenerationRequest,
    GenerationResponse,
    ModelCapabilities,
    ModelProvider,
    ModelProviderError,
    ProviderHealth,
    ProviderUnavailableError,
)
from phoenix.models.registry import (
    CapabilityRequirements,
    ModelCapabilityRecord,
    ModelCapabilityRegistry,
)


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


class FakeProvider(ModelProvider):
    """Test-only provider: other methods fail if the registry invokes them."""

    def __init__(
        self, result: ModelCapabilities, failure: Exception | None = None
    ) -> None:
        super().__init__("fixture")
        self.result = result
        self.failure = failure
        self.calls: list[str] = []

    async def capabilities(self, model_id: str) -> ModelCapabilities:
        self.calls.append(model_id)
        if self.failure is not None:
            raise self.failure
        return self.result

    async def health(self) -> ProviderHealth:
        raise AssertionError("Unexpected health call")

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        raise AssertionError("Unexpected generation call")

    def stream(self, request: GenerationRequest) -> AsyncIterator[GenerationChunk]:
        raise AssertionError("Unexpected stream call")

    async def cancel(self, operation_id: str) -> CancellationResult:
        raise AssertionError("Unexpected cancel call")


def test_empty_registry_and_pair_key_operations() -> None:
    store = ModelCapabilityRegistry()
    assert store.list_records() == ()
    assert store.matching(CapabilityRequirements()) == ()
    assert store.get("fixture", "missing") is None
    first = store.register(caps())
    second = store.register(caps(provider_id="future-provider"))
    assert first != second
    assert store.get("fixture", "model") is first
    assert store.get("future-provider", "model") is second
    assert store.remove("fixture", "model") is True
    assert store.remove("fixture", "model") is False
    assert store.get("future-provider", "model") is second
    store.clear()
    store.clear()
    assert store.list_records() == ()


def test_identifier_normalization_keeps_neutral_names() -> None:
    store = ModelCapabilityRegistry()
    record = store.register(caps(provider_id=" fixture ", model_id=" model:latest "))
    assert record.provider_id == "fixture" and record.model_id == "model:latest"
    assert store.get(" fixture ", " model:latest ") is record
    assert store.get("fixture", "model") is None
    assert store.remove(" fixture ", " model:latest ")


@pytest.mark.parametrize("method", ["get", "remove"])
@pytest.mark.parametrize(
    "ids", [("", "model"), ("fixture", " "), (1, "model"), ("fixture", None)]
)
def test_invalid_lookup_identifiers(method: str, ids: tuple[object, object]) -> None:
    store = ModelCapabilityRegistry()
    with pytest.raises(ValidationError):
        getattr(store, method)(*ids)
    assert store.list_records() == ()


def test_replacement_fresh_timestamp_and_snapshot() -> None:
    store = ModelCapabilityRegistry()
    instant = datetime(2026, 10, 4, tzinfo=UTC)
    with patch.object(registry, "datetime") as clock:
        clock.now.side_effect = [instant, instant + timedelta(seconds=1)]
        original_caps = caps()
        first = store.register(original_caps)
        second = store.register(caps(supports_streaming=True))
    assert first.refreshed_at == instant
    assert second.refreshed_at == instant + timedelta(seconds=1)
    assert second.capabilities.supports_streaming
    assert not first.capabilities.supports_streaming
    assert first.capabilities is not original_caps
    assert store.list_records() == (second,)


def test_listing_is_sorted_and_detached() -> None:
    store = ModelCapabilityRegistry()
    for provider_id, model_id in [("z", "a"), ("a", "z"), ("a", "a")]:
        store.register(caps(provider_id=provider_id, model_id=model_id))
    listing = store.list_records()
    assert isinstance(listing, tuple)
    assert [(r.provider_id, r.model_id) for r in listing] == [
        ("a", "a"),
        ("a", "z"),
        ("z", "a"),
    ]
    store.clear()
    assert len(listing) == 3 and store.list_records() == ()


def test_record_utc_immutability_and_deterministic_json() -> None:
    stamp = datetime(2026, 10, 4, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    record = ModelCapabilityRecord(
        provider_id="fixture", model_id="model", capabilities=caps(), refreshed_at=stamp
    )
    assert record.refreshed_at == stamp and record.refreshed_at.tzinfo is UTC
    assert record.model_dump_json() == record.model_dump_json()
    assert ModelCapabilityRecord.model_validate_json(record.model_dump_json()) == record
    assert json.loads(record.model_dump_json())["refreshed_at"].endswith("Z")
    with pytest.raises(ValidationError):
        record.model_id = "other"
    with pytest.raises(ValidationError):
        record.capabilities.supports_tool_use = True
    requirements = CapabilityRequirements()
    with pytest.raises(ValidationError):
        requirements.requires_streaming = True
    assert (
        CapabilityRequirements.model_validate_json(requirements.model_dump_json())
        == requirements
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"provider_id": "other"},
        {"model_id": "other"},
        {"provider_id": ""},
        {"model_id": " "},
        {"refreshed_at": datetime(2026, 1, 1)},
        {"metadata": {}},
        {"capabilities": object()},
        {"capabilities": caps().model_copy(update={"supports_vision": "yes"})},
        {"capabilities": caps().model_copy(update={"context_limit_tokens": -1})},
    ],
)
def test_invalid_records(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ModelCapabilityRecord.model_validate(
            {
                "provider_id": "fixture",
                "model_id": "model",
                "capabilities": caps(),
                **changes,
            }
        )


@pytest.mark.parametrize(
    "field",
    [
        "requires_streaming",
        "requires_structured_output",
        "requires_tool_use",
        "requires_vision",
        "requires_cancellation",
    ],
)
@pytest.mark.parametrize("value", [1, "true", None])
def test_requirement_booleans_are_strict(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        CapabilityRequirements.model_validate({field: value})


@pytest.mark.parametrize("value", [0, -1, True, "2048", 1.5])
def test_invalid_minimum_context(value: object) -> None:
    with pytest.raises(ValidationError):
        CapabilityRequirements.model_validate({"min_context_tokens": value})


@pytest.mark.parametrize("field", ["score", "tier", "health", "loaded", "metadata"])
def test_unrelated_requirements_forbidden(field: str) -> None:
    with pytest.raises(ValidationError):
        CapabilityRequirements.model_validate({field: True})


def test_invalid_registration_keeps_existing_state() -> None:
    store = ModelCapabilityRegistry()
    old = store.register(caps())
    for field, value in [
        ("supports_tool_use", "true"),
        ("context_limit_tokens", True),
        ("provider_id", ""),
    ]:
        invalid = caps().model_copy(update={field: value})
        with pytest.raises(ValidationError):
            store.register(invalid)
        assert store.list_records() == (old,)


def test_refresh_only_calls_capabilities_and_replaces() -> None:
    async def run() -> None:
        store = ModelCapabilityRegistry()
        provider: ModelProvider = FakeProvider(caps())
        first = await store.refresh(provider, " model ")
        assert first.capabilities == caps()
        assert isinstance(provider, FakeProvider) and provider.calls == ["model"]
        provider.result = caps(supports_vision=True)
        second = await store.refresh(provider, "model")
        assert second.capabilities.supports_vision
        assert store.list_records() == (second,) and provider.calls == [
            "model",
            "model",
        ]
        assert second is not first
        store.matching(CapabilityRequirements())
        store.get("fixture", "model")
        store.remove("fixture", "model")
        store.clear()
        assert provider.calls == ["model", "model"]

    asyncio.run(run())


@pytest.mark.parametrize("populated", [False, True])
@pytest.mark.parametrize(
    "failure_kind",
    ["provider-id", "model-id", "validation", "unavailable", "provider", "exception"],
)
def test_failed_refresh_is_atomic(populated: bool, failure_kind: str) -> None:
    async def run() -> None:
        store = ModelCapabilityRegistry()
        if populated:
            store.register(caps())
        before = store.list_records()
        result = caps()
        error: Exception | None = None
        expected: type[Exception] = ModelProviderError
        if failure_kind == "provider-id":
            result = caps(provider_id="other")
        elif failure_kind == "model-id":
            result = caps(model_id="other")
        elif failure_kind == "validation":
            result = caps().model_copy(update={"context_limit_tokens": -1})
            expected = ValidationError
        elif failure_kind == "unavailable":
            error = ProviderUnavailableError("fixture unavailable")
            expected = ProviderUnavailableError
        elif failure_kind == "provider":
            error = ModelProviderError("fixture error")
        else:
            error = RuntimeError("fixture exception")
            expected = RuntimeError
        provider = FakeProvider(result, error)
        with pytest.raises(expected) as failure:
            await store.refresh(provider, "model")
        if error is not None:
            assert failure.value is error
        assert store.list_records() == before
        if populated:
            assert store.get("fixture", "model") is before[0]
        assert provider.calls == ["model"]

    asyncio.run(run())


@pytest.mark.parametrize("model_id", ["", " ", 1])
def test_refresh_validates_input_before_provider_call(model_id: object) -> None:
    async def run() -> None:
        provider = FakeProvider(caps())
        store = ModelCapabilityRegistry()
        with pytest.raises(ValidationError):
            await store.refresh(provider, model_id)  # type: ignore[arg-type]
        assert not provider.calls and not store.list_records()

    asyncio.run(run())


@pytest.mark.parametrize(
    "suffix", ["streaming", "structured_output", "tool_use", "vision", "cancellation"]
)
def test_boolean_matching_requires_support_only_when_requested(suffix: str) -> None:
    store = ModelCapabilityRegistry()
    unsupported = store.register(caps(model_id="a"))
    supported = store.register(caps(model_id="b", **{f"supports_{suffix}": True}))
    assert store.matching(CapabilityRequirements()) == (unsupported, supported)
    requirements = CapabilityRequirements.model_validate({f"requires_{suffix}": True})
    assert store.matching(requirements) == (supported,)


def test_combined_matching_returns_every_match_in_key_order() -> None:
    store = ModelCapabilityRegistry()
    for provider_id, model_id, vision in [
        ("z", "model", True),
        ("a", "z", True),
        ("a", "a", False),
    ]:
        store.register(
            caps(
                provider_id=provider_id,
                model_id=model_id,
                supports_streaming=True,
                supports_tool_use=True,
                supports_vision=vision,
            )
        )
    result = store.matching(
        CapabilityRequirements(
            requires_streaming=True, requires_tool_use=True, requires_vision=True
        )
    )
    assert [(r.provider_id, r.model_id) for r in result] == [("a", "z"), ("z", "model")]


@pytest.mark.parametrize(
    "minimum,expected",
    [
        (None, ("below", "equal", "higher", "unknown")),
        (2048, ("equal", "higher")),
        (4096, ("higher",)),
        (8192, ()),
    ],
)
def test_context_matching(minimum: int | None, expected: tuple[str, ...]) -> None:
    store = ModelCapabilityRegistry()
    for name, context in [
        ("unknown", None),
        ("higher", 4096),
        ("equal", 2048),
        ("below", 1024),
    ]:
        store.register(caps(model_id=name, context_limit_tokens=context))
    assert (
        tuple(
            r.model_id
            for r in store.matching(CapabilityRequirements(min_context_tokens=minimum))
        )
        == expected
    )


def test_invalid_copied_requirements_rejected() -> None:
    store = ModelCapabilityRegistry()
    store.register(caps())
    with pytest.raises(ValidationError):
        store.matching(
            CapabilityRequirements().model_copy(update={"requires_tool_use": "yes"})
        )


@pytest.mark.parametrize("same_key", [True, False])
@pytest.mark.parametrize("late_failure", [True, False])
def test_concurrent_refresh_last_successful_completion_wins(
    same_key: bool, late_failure: bool
) -> None:
    async def run() -> None:
        store = ModelCapabilityRegistry()
        store.register(caps())
        started = [asyncio.Event(), asyncio.Event()]
        release = [asyncio.Event(), asyncio.Event()]

        class DelayedProvider(FakeProvider):
            def __init__(self, index: int, result: ModelCapabilities) -> None:
                super().__init__(result)
                self.index = index

            async def capabilities(self, model_id: str) -> ModelCapabilities:
                self.calls.append(model_id)
                started[self.index].set()
                await release[self.index].wait()
                if late_failure and self.index == 0:
                    raise ProviderUnavailableError("late failure")
                return self.result

        late = DelayedProvider(0, caps(supports_vision=True))
        early_model = "model" if same_key else "other"
        early = DelayedProvider(1, caps(model_id=early_model, supports_tool_use=True))
        tasks = [
            asyncio.create_task(store.refresh(late, "model")),
            asyncio.create_task(store.refresh(early, early_model)),
        ]
        await asyncio.gather(*(event.wait() for event in started))
        # No global lock blocks independent reads or commits during provider I/O.
        assert store.get("fixture", "model") is not None
        release[1].set()
        early_record = await tasks[1]
        assert store.get("fixture", early_model) is early_record
        release[0].set()
        if late_failure:
            with pytest.raises(ProviderUnavailableError):
                await tasks[0]
            assert store.get("fixture", early_model) is early_record
        else:
            late_record = await tasks[0]
            assert store.get("fixture", "model") is late_record
            if not same_key:
                assert store.get("fixture", "other") is early_record
        assert len(store.list_records()) == (1 if same_key else 2)

    async def bounded() -> None:
        await asyncio.wait_for(run(), 3)

    asyncio.run(bounded())


def test_cancelled_refresh_preserves_snapshot() -> None:
    async def run() -> None:
        store = ModelCapabilityRegistry()
        old = store.register(caps())
        started = asyncio.Event()

        class PendingProvider(FakeProvider):
            async def capabilities(self, model_id: str) -> ModelCapabilities:
                started.set()
                await asyncio.Event().wait()
                raise AssertionError("unreachable")

        task = asyncio.create_task(store.refresh(PendingProvider(caps()), "model"))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert store.list_records() == (old,)

    asyncio.run(run())


def test_ollama_refresh_uses_neutral_contract() -> None:
    async def run() -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            assert request.method == "POST" and request.url.path == "/api/show"
            assert json.loads(request.content) == {"model": "explicit-model"}
            return httpx.Response(
                200,
                json={
                    "capabilities": ["completion", "tools"],
                    "model_info": {"fixture.context_length": 8192},
                },
            )

        async with OllamaProvider(transport=httpx.MockTransport(handler)) as provider:
            backend: ModelProvider = provider
            store = ModelCapabilityRegistry()
            record = await store.refresh(backend, "explicit-model")
            assert isinstance(record.capabilities, ModelCapabilities)
            assert (
                record.provider_id == "ollama" and record.model_id == "explicit-model"
            )
            assert (
                record.capabilities.supports_streaming
                and record.capabilities.supports_tool_use
            )
            assert record.capabilities.supports_cancellation
            assert (
                not record.capabilities.supports_vision
                and not record.capabilities.supports_structured_output
            )
            assert record.capabilities.context_limit_tokens == 8192
            assert store.matching(CapabilityRequirements(requires_tool_use=True)) == (
                record,
            )
            assert calls == ["/api/show"]

    asyncio.run(run())


def test_record_and_requirements_have_only_ticket_fields() -> None:
    assert set(ModelCapabilityRecord.model_fields) == {
        "provider_id",
        "model_id",
        "capabilities",
        "refreshed_at",
    }
    assert set(CapabilityRequirements.model_fields) == {
        "requires_streaming",
        "requires_structured_output",
        "requires_tool_use",
        "requires_vision",
        "requires_cancellation",
        "min_context_tokens",
    }
    tree = ast.parse(inspect.getsource(registry))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(
                alias.name in {"datetime", "typing", "pydantic"} for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            assert node.module in {
                "datetime",
                "typing",
                "pydantic",
                "phoenix.models.provider",
            }


def test_registry_import_and_local_operations_are_isolated(tmp_path: Path) -> None:
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
    raise AssertionError("Unexpected registry side effect")
def audit(event, args):
    if event == "open" and args[2] & (
        os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    ):
        forbidden()
class BlockImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        roots = ("httpx", "ollama", "colibri", "psutil", "fastapi", "sqlalchemy",
                 "alembic", "phoenix.models.ollama", "phoenix.core", "phoenix.api",
                 "phoenix.persistence", "phoenix.agents", "phoenix.tools",
                 "phoenix.permissions", "phoenix.voice")
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
    ModelCapabilities, ModelProvider, ProviderHealth, GenerationRequest,
    GenerationResponse, GenerationChunk, CancellationResult,
)
from phoenix.models.registry import (
    ModelCapabilityRegistry, CapabilityRequirements, ModelCapabilityRecord,
)
"""
        + inspect.getsource(caps)
        + inspect.getsource(FakeProvider)
        + """
store = ModelCapabilityRegistry()
assert store.list_records() == ()
record = store.register(caps(supports_tool_use=True))
ModelCapabilityRecord.model_validate_json(record.model_dump_json())
requirements = CapabilityRequirements(requires_tool_use=True)
assert store.get("fixture", "model") is record
assert store.matching(requirements) == (record,)
provider = FakeProvider(caps())
coroutine = store.refresh(provider, "model")
try:
    coroutine.send(None)
except StopIteration as completed:
    assert completed.value.capabilities == caps()
else:
    raise AssertionError("Fake refresh scheduled unexpected work")
assert provider.calls == ["model"]
assert store.remove("fixture", "model")
store.clear()
assert not list(Path.cwd().iterdir())
print("Registry isolation: PASS")
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
    assert result.stdout.strip() == "Registry isolation: PASS"
