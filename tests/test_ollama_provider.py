"""In-process HTTPX tests: no test depends on a live Ollama installation."""

import ast
import asyncio
import inspect
import json
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import UTC
from pathlib import Path

import httpx
import pytest

from phoenix.models import ollama
from phoenix.models.ollama import OllamaProvider
from phoenix.models.provider import (
    CancellationStatus,
    GenerationChunk,
    GenerationRequest,
    MessageRole,
    ModelMessage,
    ModelProvider,
    ModelProviderError,
    ProviderHealthStatus,
    ProviderUnavailableError,
)


@pytest.fixture
def generation_request() -> GenerationRequest:
    return GenerationRequest(
        operation_id="operation-a",
        model_id="model",
        messages=tuple(
            ModelMessage(role=role, text=role.value) for role in MessageRole
        ),
        temperature=0,
        max_output_tokens=16,
        stop_sequences=("END",),
    )


def wire(**overrides: object) -> dict[str, object]:
    return {
        "model": "model:latest",
        "message": {"role": "assistant", "content": "PHOENIX_OK"},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 3,
        "eval_count": 2,
        "total_duration": 2_000_000_000,
        "eval_duration": 1_000_000_000,
        **overrides,
    }


class ByteStream(httpx.AsyncByteStream):
    def __init__(self, records: list[bytes]) -> None:
        self.records = records
        self.consumed = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for record in self.records:
            self.consumed += 1
            yield record

    async def aclose(self) -> None:
        self.closed = True


def encoded(record: dict[str, object]) -> bytes:
    return (json.dumps(record) + "\n").encode()


def test_construction_default_and_timeout_policy() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200)

    backend = OllamaProvider(transport=httpx.MockTransport(handler))
    assert isinstance(backend, ModelProvider)
    assert backend.provider_id == "ollama" and not calls
    assert str(backend.base_url) == "http://127.0.0.1:11434/"
    assert backend.active_operation_ids == ()
    assert backend._client.timeout.as_dict() == {
        "connect": 3,
        "read": 120,
        "write": 10,
        "pool": 5,
    }
    asyncio.run(backend.aclose())


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("HTTP://LOCALHOST:11434///", "http://localhost:11434/"),
        ("https://example.test/proxy/", "https://example.test/proxy/"),
        ("http://[::1]:11434", "http://[::1]:11434/"),
    ],
)
def test_base_url_normalization(url: str, expected: str) -> None:
    backend = OllamaProvider(url)
    assert str(backend.base_url) == expected
    asyncio.run(backend.aclose())


@pytest.mark.parametrize(
    "url",
    [
        "localhost",
        "ftp://host",
        "http:///",
        "http://bad host",
        "http://host:99999",
        "http://host:bad",
        "http://user:private@host",
        "http://host/?private",
        "http://host/#private",
        "http://host\\private",
    ],
)
def test_invalid_base_url_is_safe(url: str) -> None:
    with pytest.raises(ValueError) as failure:
        OllamaProvider(url)
    assert url not in str(failure.value) and "private" not in str(failure.value)


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True])
def test_invalid_operation_timeout(value: float) -> None:
    with pytest.raises(ValueError):
        OllamaProvider(operation_timeout=value)


@pytest.mark.parametrize(
    "timeout",
    [
        httpx.Timeout(None),
        httpx.Timeout(1, read=None),
        httpx.Timeout(-1),
        httpx.Timeout(float("inf")),
    ],
)
def test_invalid_http_timeouts(timeout: httpx.Timeout) -> None:
    with pytest.raises(ValueError):
        OllamaProvider(timeout=timeout)


def test_health_success_and_explicit_http_configuration() -> None:
    async def run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET" and request.url.path == "/api/version"
            assert request.extensions["timeout"] == {
                "connect": 1,
                "read": 2,
                "write": 3,
                "pool": 4,
            }
            return httpx.Response(200, json={"version": "0.35.1"})

        async with OllamaProvider(
            timeout=httpx.Timeout(connect=1, read=2, write=3, pool=4),
            transport=httpx.MockTransport(handler),
        ) as backend:
            health = await backend.health()
            assert health.status is ProviderHealthStatus.HEALTHY
            assert health.provider_id == "ollama" and health.checked_at.tzinfo is UTC
            assert health.detail == "Ollama version 0.35.1"

    asyncio.run(run())


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ReadError],
)
def test_unreachable_health_and_generation(
    error: type[httpx.RequestError], generation_request: GenerationRequest
) -> None:
    async def run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise error("private transport diagnostic", request=request)

        async with OllamaProvider(transport=httpx.MockTransport(handler)) as backend:
            assert (await backend.health()).status is ProviderHealthStatus.UNAVAILABLE
            with pytest.raises(ProviderUnavailableError) as failure:
                await backend.generate(generation_request)
            assert "private" not in str(failure.value)
            assert backend.active_operation_ids == ()

    asyncio.run(run())


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"version": ""},
        {"version": 35},
        {"version": "secret\nprivate"},
        [],
        {"error": "private"},
    ],
)
def test_malformed_health_is_not_healthy(payload: object) -> None:
    async def run() -> None:
        async with OllamaProvider(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload))
        ) as backend:
            health = await backend.health()
            assert health.status is ProviderHealthStatus.DEGRADED
            assert health.detail and "private" not in health.detail

    asyncio.run(run())


@pytest.mark.parametrize("status", [301, 307, 404, 500])
def test_http_failures_and_no_redirect_following(
    status: int, generation_request: GenerationRequest
) -> None:
    async def run() -> None:
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(
                status,
                headers={"location": "https://other.test/private"},
                json={"error": "private raw body"},
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=True
        ) as client:
            backend = OllamaProvider(client=client)
            with pytest.raises(ModelProviderError) as failure:
                await backend.generate(generation_request)
            assert str(status) in str(failure.value) and "private" not in str(
                failure.value
            )
            assert len(calls) == 1 and backend.active_operation_ids == ()
            await backend.aclose()
            assert not client.is_closed

    asyncio.run(run())


@pytest.mark.parametrize(
    "values",
    [
        ["completion", "tools", "vision"],
        ["embedding"],
        [],
        None,
        "completion",
        ["completion", 1],
    ],
)
def test_capability_mapping(values: object) -> None:
    async def run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "POST" and request.url.path == "/api/show"
            assert json.loads(request.content) == {"model": "neutral-model"}
            return httpx.Response(
                200,
                json={
                    "capabilities": values,
                    "model_info": {"llama.context_length": 8192},
                },
            )

        async with OllamaProvider(transport=httpx.MockTransport(handler)) as backend:
            caps = await backend.capabilities("neutral-model")
            supported = values == ["completion", "tools", "vision"]
            assert caps.supports_streaming == supported
            assert (
                caps.supports_tool_use == supported
                and caps.supports_vision == supported
            )
            assert not caps.supports_structured_output and caps.supports_cancellation
            assert caps.context_limit_tokens == 8192
            assert caps.model_id == "neutral-model" and caps.provider_id == "ollama"

    asyncio.run(run())


@pytest.mark.parametrize(
    ("info", "expected"),
    [
        (None, None),
        ({}, None),
        ({"architecture.context_length": 16384}, 16384),
        ({"a.context_length": 1024, "b.context_length": 1024}, 1024),
        ({"a.context_length": 1024, "b.context_length": 2048}, None),
        ({"a.context_length": 0}, None),
        ({"a.context_length": -1}, None),
        ({"a.context_length": True}, None),
        ({"a.context_length": "16384"}, None),
        ({"a.context_length": 1.5}, None),
        ({"a.context_length": 16384, "b.context_length": None}, None),
    ],
)
def test_context_limit_conservative(info: object, expected: int | None) -> None:
    async def run() -> None:
        async with OllamaProvider(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, json={"model_info": info})
            )
        ) as backend:
            assert (
                await backend.capabilities("model")
            ).context_limit_tokens == expected

    asyncio.run(run())


def test_nonstream_mapping_and_substitutability(
    generation_request: GenerationRequest,
) -> None:
    async def consume(backend: ModelProvider) -> None:
        response = await backend.generate(generation_request)
        assert response.operation_id == generation_request.operation_id
        assert response.model_id == "model" and response.provider_id == "ollama"
        assert response.text == "PHOENIX_OK" and response.finish_reason == "stop"
        assert response.usage and response.usage.input_tokens == 3
        assert response.usage.output_tokens == 2 and response.usage.total_tokens is None
        assert response.metrics and response.metrics.duration_ms == 2000
        assert (
            response.metrics.tokens_per_second == 2 and response.metrics.ttft_ms is None
        )

    async def run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "POST" and request.url.path == "/api/chat"
            body = json.loads(request.content)
            assert body == {
                "model": "model",
                "messages": [
                    {"role": role.value, "content": role.value} for role in MessageRole
                ],
                "stream": False,
                "options": {"temperature": 0, "num_predict": 16, "stop": ["END"]},
            }
            return httpx.Response(200, json=wire())

        async with OllamaProvider(transport=httpx.MockTransport(handler)) as backend:
            await consume(backend)
            assert backend.active_operation_ids == ()
            assert (
                await backend.cancel(generation_request.operation_id)
            ).status is CancellationStatus.NOT_FOUND

    asyncio.run(run())


@pytest.mark.parametrize("model", ["model", "model:latest"])
def test_latest_equivalence(model: str, generation_request: GenerationRequest) -> None:
    async def run() -> None:
        async with OllamaProvider(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, json=wire(model=model))
            )
        ) as backend:
            request = generation_request.model_copy(update={"model_id": "model:latest"})
            assert (await backend.generate(request)).model_id == "model:latest"

    asyncio.run(run())


@pytest.mark.parametrize(
    "changes",
    [
        {"model": "other"},
        {"model": "model:other"},
        {"model": None},
        {"message": {}},
        {"message": {"content": 1}},
        {"message": {"role": "user", "content": "text"}},
        {"done": False},
        {"done": "true"},
        {"eval_count": -1},
        {"eval_count": True},
        {"prompt_eval_count": "3"},
        {"total_duration": -1},
        {"total_duration": float("nan")},
        {"eval_duration": float("inf")},
        {"load_duration": -1},
        {"prompt_eval_duration": -1},
        {"total_duration": 2**1100},
        {"error": "private"},
    ],
)
def test_invalid_generation_records(
    changes: dict[str, object], generation_request: GenerationRequest
) -> None:
    async def run() -> None:
        async with OllamaProvider(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, content=json.dumps(wire(**changes)))
            )
        ) as backend:
            with pytest.raises(ModelProviderError):
                await backend.generate(generation_request)
            assert backend.active_operation_ids == ()

    asyncio.run(run())


@pytest.mark.parametrize(
    "payload",
    [
        "bad JSON",
        "[]",
        "{}",
        '{"message":{"content":"text"},"done":true,"eval_count":NaN}',
    ],
)
def test_invalid_json_response(
    payload: str, generation_request: GenerationRequest
) -> None:
    async def run() -> None:
        async with OllamaProvider(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, content=payload)
            )
        ) as backend:
            with pytest.raises(ModelProviderError):
                await backend.generate(generation_request)
            assert backend.active_operation_ids == ()

    asyncio.run(run())


@pytest.mark.parametrize("eval_duration", [None, 0])
def test_no_inferred_throughput(
    eval_duration: int | None, generation_request: GenerationRequest
) -> None:
    async def run() -> None:
        async with OllamaProvider(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, json=wire(eval_duration=eval_duration))
            )
        ) as backend:
            response = await backend.generate(generation_request)
            assert response.metrics and response.metrics.tokens_per_second is None

    asyncio.run(run())


def test_unknown_optional_fields_and_no_options(
    generation_request: GenerationRequest,
) -> None:
    async def run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert "options" not in json.loads(request.content)
            return httpx.Response(200, json={"message": {"content": ""}, "done": True})

        async with OllamaProvider(transport=httpx.MockTransport(handler)) as backend:
            request = generation_request.model_copy(
                update={
                    "temperature": None,
                    "max_output_tokens": None,
                    "stop_sequences": (),
                }
            )
            response = await backend.generate(request)
            assert (
                response.finish_reason is None
                and response.usage is None
                and response.metrics is None
            )

    asyncio.run(run())


def test_incremental_stream_and_final_mapping(
    generation_request: GenerationRequest,
) -> None:
    async def run() -> None:
        stream = ByteStream(
            [
                b"\n",
                encoded(
                    wire(message={"role": "assistant", "content": "one"}, done=False)
                ),
                encoded(wire(message={"content": "two"}, done=False)),
                encoded(wire(message={"content": "last"})),
            ]
        )

        def handler(request: httpx.Request) -> httpx.Response:
            assert (
                request.url.path == "/api/chat"
                and json.loads(request.content)["stream"] is True
            )
            return httpx.Response(200, stream=stream)

        async with OllamaProvider(transport=httpx.MockTransport(handler)) as backend:
            iterator = backend.stream(generation_request)
            first = await anext(iterator)
            assert first.text == "one" and stream.consumed == 2 and not stream.closed
            rest = [chunk async for chunk in iterator]
            chunks = [first, *rest]
            assert [chunk.text for chunk in chunks] == ["one", "two", "last"]
            assert sum(chunk.is_final for chunk in chunks) == 1 and chunks[-1].is_final
            assert all(isinstance(chunk, GenerationChunk) for chunk in chunks)
            assert all(
                chunk.operation_id == generation_request.operation_id
                and chunk.model_id == "model"
                and chunk.provider_id == "ollama"
                for chunk in chunks
            )
            final = chunks[-1]
            assert (
                final.finish_reason == "stop"
                and final.usage
                and final.usage.output_tokens == 2
            )
            assert final.usage.total_tokens is None
            assert (
                final.metrics
                and final.metrics.duration_ms == 2000
                and final.metrics.tokens_per_second == 2
            )
            assert stream.closed and backend.active_operation_ids == ()

    asyncio.run(run())


@pytest.mark.parametrize(
    "records",
    [
        [b"malformed\n"],
        [encoded(wire(done=False))],
        [encoded({"done": True})],
        [encoded(wire(model="other"))],
        [encoded({"error": "private"})],
        [encoded(wire(done="true"))],
        [encoded(wire(eval_count=-1))],
        [],
    ],
)
def test_stream_failure_not_fake_completion(
    records: list[bytes], generation_request: GenerationRequest
) -> None:
    async def run() -> None:
        source = ByteStream(records)
        async with OllamaProvider(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, stream=source)
            )
        ) as backend:
            chunks: list[GenerationChunk] = []
            with pytest.raises(ModelProviderError):
                async for chunk in backend.stream(generation_request):
                    chunks.append(chunk)
            assert not any(chunk.is_final for chunk in chunks)
            assert source.closed and backend.active_operation_ids == ()

    asyncio.run(run())


@pytest.mark.parametrize("status", [307, 404, 500])
def test_stream_http_failure(
    status: int, generation_request: GenerationRequest
) -> None:
    async def run() -> None:
        source = ByteStream([b"private error body"])
        async with OllamaProvider(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(status, stream=source)
            )
        ) as backend:
            with pytest.raises(ModelProviderError) as failure:
                await anext(backend.stream(generation_request))
            assert "private" not in str(failure.value)
            assert source.closed and backend.active_operation_ids == ()

    asyncio.run(run())


def test_cancellation_is_scoped_duplicate_rejected(
    generation_request: GenerationRequest,
) -> None:
    async def run() -> None:
        started: dict[str, asyncio.Event] = {key: asyncio.Event() for key in ("a", "b")}
        releases: dict[str, asyncio.Event] = {
            key: asyncio.Event() for key in ("a", "b")
        }

        async def handler(request: httpx.Request) -> httpx.Response:
            key = json.loads(request.content)["messages"][0]["content"]
            started[key].set()
            await releases[key].wait()
            return httpx.Response(200, json=wire())

        async with OllamaProvider(transport=httpx.MockTransport(handler)) as backend:
            request_a = generation_request.model_copy(
                update={"messages": (ModelMessage(role=MessageRole.USER, text="a"),)}
            )
            request_b = request_a.model_copy(
                update={
                    "operation_id": "operation-b",
                    "messages": (ModelMessage(role=MessageRole.USER, text="b"),),
                }
            )
            task_a = asyncio.create_task(backend.generate(request_a))
            task_b = asyncio.create_task(backend.generate(request_b))
            await asyncio.wait_for(
                asyncio.gather(*(event.wait() for event in started.values())), 1
            )
            with pytest.raises(ModelProviderError, match="duplicate"):
                await backend.generate(request_a)
            assert set(backend.active_operation_ids) == {"operation-a", "operation-b"}
            result = await backend.cancel("operation-a")
            assert (
                result.status is CancellationStatus.ACCEPTED
                and result.operation_id == "operation-a"
                and result.provider_id == "ollama"
            )
            with pytest.raises(asyncio.CancelledError):
                await task_a
            assert (
                backend.active_operation_ids == ("operation-b",) and not task_b.done()
            )
            releases["b"].set()
            assert (await task_b).text == "PHOENIX_OK"
            assert backend.active_operation_ids == ()
            assert (
                await backend.cancel("unknown")
            ).status is CancellationStatus.NOT_FOUND

    asyncio.run(run())


def test_suspended_stream_cancel_does_not_cancel_consumer(
    generation_request: GenerationRequest,
) -> None:
    async def run() -> None:
        source = ByteStream([encoded(wire(done=False)), encoded(wire())])
        async with OllamaProvider(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, stream=source)
            )
        ) as backend:
            iterator = backend.stream(generation_request)
            await anext(iterator)
            assert (
                await backend.cancel(generation_request.operation_id)
            ).status is CancellationStatus.ACCEPTED
            await asyncio.sleep(0)
            assert source.closed and backend.active_operation_ids == ()
            with pytest.raises(asyncio.CancelledError):
                await anext(iterator)
            task = asyncio.current_task()
            assert task is not None and not task.cancelling()

    asyncio.run(run())


def test_stream_explicit_early_close(generation_request: GenerationRequest) -> None:
    async def run() -> None:
        source = ByteStream([encoded(wire(done=False)), encoded(wire())])
        async with OllamaProvider(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, stream=source)
            )
        ) as backend:
            iterator = backend.stream(generation_request)
            await anext(iterator)
            await iterator.aclose()
            assert source.closed and backend.active_operation_ids == ()

    asyncio.run(run())


@pytest.mark.parametrize("action", ["cancel", "close", "timeout", "disconnect"])
def test_blocked_stream_cleanup(
    action: str, generation_request: GenerationRequest
) -> None:
    async def run() -> None:
        reading = asyncio.Event()

        class BlockedStream(ByteStream):
            async def __aiter__(self) -> AsyncIterator[bytes]:
                yield encoded(wire(done=False))
                reading.set()
                if action == "disconnect":
                    raise httpx.ReadError("private transport detail")
                await asyncio.Event().wait()

        source = BlockedStream([])
        backend = OllamaProvider(
            operation_timeout=0.05 if action == "timeout" else 300,
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, stream=source)
            ),
        )
        iterator = backend.stream(generation_request)
        await anext(iterator)
        task = asyncio.create_task(anext(iterator))
        await asyncio.wait_for(reading.wait(), 1)
        if action == "cancel":
            assert (
                await backend.cancel(generation_request.operation_id)
            ).status is CancellationStatus.ACCEPTED
        elif action == "close":
            await backend.aclose()
        if action in ("cancel", "close"):
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(ProviderUnavailableError) as failure:
                await task
            assert "private" not in str(failure.value)
        assert source.closed and backend.active_operation_ids == ()
        await backend.aclose()

    asyncio.run(run())


def test_stream_deadline_between_chunks_does_not_cancel_consumer(
    generation_request: GenerationRequest,
) -> None:
    async def run() -> None:
        source = ByteStream([encoded(wire(done=False)), encoded(wire())])
        async with OllamaProvider(
            operation_timeout=0.01,
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, stream=source)
            ),
        ) as backend:
            iterator = backend.stream(generation_request)
            await anext(iterator)
            await asyncio.sleep(0.02)
            with pytest.raises(ProviderUnavailableError):
                await anext(iterator)
            assert source.closed and backend.active_operation_ids == ()
            task = asyncio.current_task()
            assert task is not None and not task.cancelling()

    asyncio.run(run())


def test_total_deadline_cleans_operations(
    generation_request: GenerationRequest,
) -> None:
    async def run() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async with OllamaProvider(
            operation_timeout=0.01, transport=httpx.MockTransport(handler)
        ) as backend:
            assert (await backend.health()).status is ProviderHealthStatus.UNAVAILABLE
            with pytest.raises(ProviderUnavailableError):
                await backend.generate(generation_request)
            assert backend.active_operation_ids == ()
            with pytest.raises(ProviderUnavailableError):
                await anext(backend.stream(generation_request))
            assert backend.active_operation_ids == ()

    asyncio.run(run())


def test_close_cancels_pending_operations_and_owns_client(
    generation_request: GenerationRequest,
) -> None:
    async def run() -> None:
        started = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        backend = OllamaProvider(transport=httpx.MockTransport(handler))
        task = asyncio.create_task(backend.generate(generation_request))
        await asyncio.wait_for(started.wait(), 1)
        await backend.aclose()
        assert task.cancelled() and backend.active_operation_ids == ()
        assert backend._client.is_closed
        await backend.aclose()
        with pytest.raises(ModelProviderError, match="closed"):
            await backend.generate(generation_request)

    asyncio.run(run())


def test_close_closes_suspended_stream(generation_request: GenerationRequest) -> None:
    async def run() -> None:
        source = ByteStream([encoded(wire(done=False)), encoded(wire())])
        backend = OllamaProvider(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, stream=source)
            )
        )
        iterator = backend.stream(generation_request)
        await anext(iterator)
        await backend.aclose()
        assert source.closed and backend.active_operation_ids == ()
        with pytest.raises(asyncio.CancelledError):
            await anext(iterator)

    asyncio.run(run())


def test_no_forbidden_imports() -> None:
    tree = ast.parse(inspect.getsource(ollama))
    allowed = {
        "asyncio",
        "json",
        "math",
        "re",
        "collections",
        "dataclasses",
        "typing",
        "urllib",
        "httpx",
        "pydantic",
        "phoenix.models.provider",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name in allowed for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module and (
                node.module in allowed or node.module.split(".")[0] in allowed
            )


def test_import_construction_and_mock_operations_are_isolated(tmp_path: Path) -> None:
    script = """
import asyncio
import importlib.abc
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import threading

loop = asyncio.new_event_loop()
def forbidden(*args, **kwargs):
    raise AssertionError("Unexpected runtime side effect")
def audit(event, args):
    if event == "open" and args[2] & (
        os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    ):
        forbidden()
class BlockImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        roots = ("fastapi", "sqlalchemy", "alembic", "psutil", "ollama", "colibri",
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
import httpx
from phoenix.models.ollama import OllamaProvider
from phoenix.models.provider import GenerationRequest, ModelMessage, MessageRole

# The default owned client must construct without sockets, probes or tasks.
default = OllamaProvider()
calls = []
def handler(request):
    calls.append(request.url.path)
    if request.url.path == "/api/version":
        return httpx.Response(200, json={"version": "0.35.1"})
    if request.url.path == "/api/show":
        return httpx.Response(200, json={"capabilities": ["completion"]})
    return httpx.Response(200, json={"message": {"content": "fixture"}, "done": True})
backend = OllamaProvider(transport=httpx.MockTransport(handler))
assert not calls
async def run():
    asyncio.get_running_loop().create_task = forbidden
    request = GenerationRequest(model_id="model", messages=(
        ModelMessage(role=MessageRole.TOOL, text="content only"),))
    await backend.health()
    await backend.capabilities("model")
    await backend.generate(request)
    chunks = [chunk async for chunk in backend.stream(request)]
    assert len(chunks) == 1 and chunks[0].is_final
    await backend.aclose()
    await default.aclose()
    assert not list(Path.cwd().iterdir())
    print("Ollama construction/mock I/O isolation: PASS")
try:
    loop.run_until_complete(run())
finally:
    loop.close()
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert result.stdout.strip() == "Ollama construction/mock I/O isolation: PASS"
