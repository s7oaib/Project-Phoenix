"""PHX-014 contracts and mocked REST boundaries; no live Ollama required."""

import ast
import asyncio
import inspect
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError

from phoenix.models import embeddings, ollama_embeddings
from phoenix.models.embeddings import (
    EmbeddingProvider,
    EmbeddingProviderError,
    EmbeddingRequest,
    EmbeddingResponse,
    EmbeddingUnavailableError,
)
from phoenix.models.ollama_embeddings import OllamaEmbeddingProvider


class FakeProvider(EmbeddingProvider):
    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        return EmbeddingResponse(
            operation_id=request.operation_id,
            provider_id=self.provider_id,
            model_id=request.model_id,
            embeddings=tuple(
                (float(index), -0.5) for index in range(len(request.inputs))
            ),
            dimensions=2,
        )


def request(**changes: object) -> EmbeddingRequest:
    return EmbeddingRequest.model_validate(
        {
            "operation_id": "operation",
            "model_id": "embeddinggemma",
            "inputs": (" first ", "second"),
            **changes,
        }
    )


def neutral(**changes: object) -> EmbeddingResponse:
    return EmbeddingResponse.model_validate(
        {
            "operation_id": "operation",
            "provider_id": "fixture",
            "model_id": "model",
            "embeddings": [[1, -0.5], [0, 0.25]],
            "dimensions": 2,
            **changes,
        }
    )


def wire(**changes: object) -> dict[str, object]:
    return {
        "model": "embeddinggemma:latest",
        "embeddings": [[1, -0.5], [0, 0.25]],
        **changes,
    }


def test_abc_fake_identity_and_correlation() -> None:
    with pytest.raises(TypeError):
        EmbeddingProvider("fixture")  # type: ignore[abstract]
    provider: EmbeddingProvider = FakeProvider(" fixture ")
    result = asyncio.run(provider.embed(request()))
    assert provider.provider_id == "fixture"
    assert result.operation_id == "operation" and result.model_id == "embeddinggemma"
    assert result.provider_id == provider.provider_id
    assert result.embeddings == ((0.0, -0.5), (1.0, -0.5))
    with pytest.raises(AttributeError):
        provider.provider_id = "other"  # type: ignore[misc]
    generated = EmbeddingRequest(model_id="model", inputs=("text",))
    assert UUID(generated.operation_id).version == 4
    assert (
        generated.operation_id
        != EmbeddingRequest(model_id="model", inputs=("text",)).operation_id
    )


@pytest.mark.parametrize("value", ["", " ", None, True, 1])
def test_provider_id_validation(value: object) -> None:
    with pytest.raises(ValidationError):
        FakeProvider(value)  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["model_id", "operation_id"])
@pytest.mark.parametrize("value", ["", " \n", None, True, 1])
def test_request_identifiers(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        request(**{field: value})


@pytest.mark.parametrize(
    "value", [(), [], "text", "", [""], [" \t\n"], ["valid", ""], [None], [1], [True]]
)
def test_input_validation(value: object) -> None:
    with pytest.raises(ValidationError):
        request(inputs=value)


def test_preserved_text_snapshots_frozen_json_and_no_extra_fields() -> None:
    inputs = [" second \n", "first"]
    req = request(inputs=inputs, operation_id=" supplied ")
    inputs.reverse()
    assert req.inputs == (" second \n", "first") and req.operation_id == "supplied"
    vectors = [[1, -0.5], [0, 0.25]]
    result = neutral(embeddings=vectors)
    vectors[0][0] = 99
    assert result.embeddings == ((1.0, -0.5), (0.0, 0.25))
    with pytest.raises(TypeError):
        result.embeddings[0][0] = 99  # type: ignore[index]
    for model in (req, result):
        encoded = model.model_dump_json()
        assert encoded == model.model_dump_json()
        assert type(model).model_validate_json(encoded) == model
        with pytest.raises(ValidationError):
            model.operation_id = "changed"
        with pytest.raises(ValidationError):
            type(model).model_validate({**model.model_dump(), "raw_metadata": {}})


@pytest.mark.parametrize(
    "value",
    [True, False, float("nan"), float("inf"), -float("inf"), "1", None, {}, object()],
)
def test_invalid_vector_numbers(value: object) -> None:
    with pytest.raises(ValidationError):
        neutral(embeddings=[[value]], dimensions=1)


@pytest.mark.parametrize("vectors", [[], [[]], [[1], [1, 2]], "text", [None]])
def test_invalid_neutral_shape(vectors: object) -> None:
    with pytest.raises(ValidationError):
        neutral(embeddings=vectors)


@pytest.mark.parametrize("dimension", [0, -1, True, "2", None, 1, 3])
def test_neutral_dimension_matches_actual_vectors(dimension: object) -> None:
    with pytest.raises(ValidationError):
        neutral(dimensions=dimension)


@pytest.mark.parametrize("field", ["provider_id", "model_id", "operation_id"])
@pytest.mark.parametrize("value", ["", " ", 1])
def test_response_identity_validation(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        neutral(**{field: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("input_tokens", -1),
        ("input_tokens", True),
        ("input_tokens", 1.5),
        ("duration_ms", -1),
        ("duration_ms", True),
        ("duration_ms", float("inf")),
    ],
)
def test_neutral_metadata_validation(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        neutral(**{field: value})


@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://127.0.0.1:11434", "http://127.0.0.1:11434/"),
        ("HTTP://LOCALHOST:11434///", "http://localhost:11434/"),
        ("https://example.test/proxy/", "https://example.test/proxy/"),
        ("http://[::1]:11434", "http://[::1]:11434/"),
    ],
)
def test_construction_normalization_and_defaults(url: str, expected: str) -> None:
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(200)

    backend = OllamaEmbeddingProvider(
        url,
        transport=httpx.MockTransport(handler),
    )
    assert isinstance(backend, EmbeddingProvider) and backend.provider_id == "ollama"
    assert str(backend.base_url) == expected and not calls
    assert backend._client.timeout.as_dict() == {
        "connect": 3,
        "read": 120,
        "write": 10,
        "pool": 5,
    }
    assert backend._operation_timeout == 300 and backend._cleanup_timeout == 5
    asyncio.run(backend.aclose())


@pytest.mark.parametrize(
    "url",
    [
        "localhost",
        "ftp://host",
        "http:///",
        "http://bad host",
        "http://host:0",
        "http://host:99999",
        "http://host:bad",
        "http://user:private@host",
        "http://host/?private",
        "http://host/#private",
        "http://host\\private",
        "http://host/\x00private",
    ],
)
def test_invalid_url_does_not_leak(url: str) -> None:
    with pytest.raises(ValueError) as failure:
        OllamaEmbeddingProvider(url)
    assert "private" not in str(failure.value) and url not in str(failure.value)


@pytest.mark.parametrize("value", [None, True, 0, -1, float("inf"), float("nan")])
@pytest.mark.parametrize(
    "phase", ["connect", "read", "write", "pool", "operation", "cleanup"]
)
def test_invalid_timeout_configuration(value: object, phase: str) -> None:
    options: dict[str, object] = {}
    if phase in ("operation", "cleanup"):
        options[phase + "_timeout"] = value
    else:
        options["timeout"] = httpx.Timeout(1, **{phase: value})  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        OllamaEmbeddingProvider(**options)  # type: ignore[arg-type]


@pytest.mark.parametrize("inputs", [(" single \n",), (" first ", "second")])
@pytest.mark.parametrize(
    "returned_model", ["embeddinggemma", "embeddinggemma:latest", "omitted"]
)
def test_success_mapping_and_optional_metadata(
    inputs: tuple[str, ...], returned_model: str
) -> None:
    async def run() -> None:
        req = request(inputs=inputs)
        vectors = [[index, -0.5, 0.25] for index in range(len(inputs))]

        def handler(received: httpx.Request) -> httpx.Response:
            assert received.method == "POST" and received.url.path == "/proxy/api/embed"
            assert json.loads(received.content) == {
                "model": req.model_id,
                "input": list(inputs),
                "truncate": False,
            }
            assert received.extensions["timeout"] == {
                "connect": 1,
                "read": 2,
                "write": 3,
                "pool": 4,
            }
            data: dict[str, object] = {
                "embeddings": vectors,
                "prompt_eval_count": 0,
                "total_duration": 1_234_567,
                "unexposed": "private",
            }
            if returned_model != "omitted":
                data["model"] = returned_model
            return httpx.Response(200, json=data)

        async with OllamaEmbeddingProvider(
            "https://example.test/proxy",
            transport=httpx.MockTransport(handler),
            timeout=httpx.Timeout(connect=1, read=2, write=3, pool=4),
        ) as backend:
            result = await backend.embed(req)
            assert (result.operation_id, result.provider_id, result.model_id) == (
                req.operation_id,
                "ollama",
                req.model_id,
            )
            assert result.embeddings == tuple(
                tuple(float(value) for value in vector) for vector in vectors
            )
            assert result.dimensions == 3 and result.input_tokens == 0
            assert result.duration_ms == 1.234567
            assert "private" not in result.model_dump_json()

    asyncio.run(run())


def test_latest_request_identity_and_unknown_metadata() -> None:
    async def run() -> None:
        async with OllamaEmbeddingProvider(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, json=wire(model="embeddinggemma"))
            )
        ) as backend:
            result = await backend.embed(request(model_id="embeddinggemma:latest"))
            assert result.model_id == "embeddinggemma:latest"
            assert result.input_tokens is None and result.duration_ms is None

    asyncio.run(run())


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        "private",
        {},
        {"error": "private"},
        wire(model="other"),
        wire(model="embeddinggemma:other"),
        wire(model=None),
        wire(model=1),
        wire(model=""),
        wire(embeddings=None),
        wire(embeddings={}),
        wire(embeddings="private"),
        wire(embeddings=[]),
        wire(embeddings=[[1]]),
        wire(embeddings=[[1], [2], [3]]),
        wire(embeddings=[[], []]),
        wire(embeddings=[[1], [1, 2]]),
        wire(embeddings=[[True], [1]]),
        wire(embeddings=[["private"], [1]]),
        wire(embeddings=[[float("nan")], [1]]),
        wire(embeddings=[[float("inf")], [1]]),
        wire(embeddings=[[-float("inf")], [1]]),
        wire(dimensions=1),
        wire(dimensions=True),
        wire(prompt_eval_count=-1),
        wire(prompt_eval_count=True),
        wire(prompt_eval_count=1.5),
        wire(prompt_eval_count="1"),
        wire(total_duration=-1),
        wire(total_duration=True),
        wire(total_duration="1"),
        wire(total_duration=float("inf")),
        wire(total_duration=10**400),
    ],
)
def test_untrusted_provider_data_fails_safely(data: object) -> None:
    async def run() -> None:
        async with OllamaEmbeddingProvider(
            "https://private.test/secret",
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, content=json.dumps(data))
            ),
        ) as backend:
            with pytest.raises(EmbeddingProviderError) as failure:
                await backend.embed(request(inputs=("private input text", "second")))
            assert not isinstance(failure.value, EmbeddingUnavailableError)
            assert all(
                secret not in str(failure.value)
                for secret in ("private", "secret", "second", "embeddings", "http")
            )
            assert failure.value.__suppress_context__

    asyncio.run(run())


@pytest.mark.parametrize("status", [301, 302, 307, 400, 404, 500])
def test_http_errors_and_redirects_even_borrowed_client(status: int) -> None:
    async def run() -> None:
        calls: list[str] = []

        def handler(req: httpx.Request) -> httpx.Response:
            calls.append(str(req.url))
            return httpx.Response(
                status,
                text="private input text",
                headers={"location": "https://remote.test"},
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=True
        ) as client:
            backend = OllamaEmbeddingProvider(client=client)
            with pytest.raises(EmbeddingProviderError) as failure:
                await backend.embed(request())
            assert "private" not in str(failure.value)
            assert len(calls) == 1
            await backend.aclose()
            assert not client.is_closed

    asyncio.run(run())


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError,
        httpx.ReadError,
        httpx.WriteError,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.WriteTimeout,
        httpx.PoolTimeout,
    ],
)
def test_transport_unavailability(error: type[httpx.HTTPError]) -> None:
    async def run() -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            raise error("private URL and input")

        async with OllamaEmbeddingProvider(
            transport=httpx.MockTransport(handler)
        ) as backend:
            with pytest.raises(EmbeddingUnavailableError) as failure:
                await backend.embed(request())
            assert "private" not in str(failure.value)

    asyncio.run(run())


@pytest.mark.parametrize("body", [b"not-json-private", b"{", b"\xff"])
def test_malformed_json(body: bytes) -> None:
    async def run() -> None:
        async with OllamaEmbeddingProvider(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, content=body))
        ) as backend:
            with pytest.raises(EmbeddingProviderError):
                await backend.embed(request())

    asyncio.run(run())


def test_total_timeout_cancellation_and_retry() -> None:
    async def run() -> None:
        entered = asyncio.Event()
        finished = asyncio.Event()
        wait = True

        async def handler(req: httpx.Request) -> httpx.Response:
            if wait:
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    finished.set()
            return httpx.Response(200, json=wire())

        async with OllamaEmbeddingProvider(
            transport=httpx.MockTransport(handler), operation_timeout=0.02
        ) as backend:
            with pytest.raises(EmbeddingUnavailableError):
                await backend.embed(request())
            assert finished.is_set()
            entered.clear()
            finished.clear()
            task = asyncio.create_task(backend.embed(request()))
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert finished.is_set()
            wait = False
            assert (await backend.embed(request())).dimensions == 2

    asyncio.run(run())


def test_owned_cleanup_closed_gate_and_exclusive_injection() -> None:
    async def run() -> None:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: httpx.Response(200))
        )
        try:
            with pytest.raises(ValueError):
                OllamaEmbeddingProvider(
                    client=client,
                    transport=httpx.MockTransport(lambda req: httpx.Response(200)),
                )
        finally:
            await client.aclose()
        backend = OllamaEmbeddingProvider()
        assert not backend._client.is_closed
        await backend.aclose()
        assert backend._client.is_closed
        await backend.aclose()
        with pytest.raises(EmbeddingProviderError, match="closed"):
            await backend.embed(request())

    asyncio.run(run())


def test_cleanup_deadline_is_safe() -> None:
    class HangingTransport(httpx.AsyncBaseTransport):
        async def aclose(self) -> None:
            await asyncio.Event().wait()

    async def run() -> None:
        backend = OllamaEmbeddingProvider(
            transport=HangingTransport(), cleanup_timeout=0.01
        )
        with pytest.raises(EmbeddingProviderError, match="cleanup"):
            await backend.aclose()

    asyncio.run(run())


def test_request_revalidation_prevents_construct_bypass() -> None:
    async def run() -> None:
        calls: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            calls.append(req)
            return httpx.Response(200)

        async with OllamaEmbeddingProvider(
            transport=httpx.MockTransport(handler)
        ) as backend:
            with pytest.raises(EmbeddingProviderError, match="request"):
                await backend.embed(request().model_copy(update={"inputs": ()}))
            assert not calls

    asyncio.run(run())


def test_float_convertible_object_is_rejected() -> None:
    class Impostor:
        def __float__(self) -> float:
            return 1.0

    with pytest.raises(ValidationError):
        neutral(embeddings=[[Impostor()]], dimensions=1)


def test_owned_client_security_configuration() -> None:
    with patch("phoenix.models.ollama_embeddings.httpx.AsyncClient") as factory:
        OllamaEmbeddingProvider("https://example.test")
        assert factory.call_args.kwargs["trust_env"] is False
        assert factory.call_args.kwargs["follow_redirects"] is False
        # HTTPX verifies TLS by default; no override disables verification.
        assert factory.call_args.kwargs.get("verify", True) is True


def test_cross_loop_use_fails_before_io() -> None:
    backend = OllamaEmbeddingProvider(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=wire()))
    )
    asyncio.run(backend.embed(request()))
    with pytest.raises(EmbeddingProviderError, match="event loop"):
        asyncio.run(backend.embed(request()))
    asyncio.run(backend.aclose())


def test_closed_borrowed_client_is_safe() -> None:
    async def run() -> None:
        client = httpx.AsyncClient()
        await client.aclose()
        backend = OllamaEmbeddingProvider(client=client)
        with pytest.raises(EmbeddingProviderError) as failure:
            await backend.embed(request())
        assert "http" not in str(failure.value)
        await backend.aclose()
        await backend.aclose()

    asyncio.run(run())


def test_modules_have_only_required_dependencies_and_endpoint() -> None:
    for module, allowed in (
        (embeddings, {"abc", "typing", "uuid", "pydantic", "phoenix.models.provider"}),
        (
            ollama_embeddings,
            {
                "asyncio",
                "math",
                "typing",
                "urllib.parse",
                "httpx",
                "pydantic",
                "phoenix.models.provider",
                "phoenix.models.embeddings",
            },
        ),
    ):
        source = inspect.getsource(module)
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                assert all(alias.name in allowed for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert node.module in allowed
        assert not any(
            endpoint in source
            for endpoint in (
                "api/pull",
                "api/tags",
                "api/chat",
                "api/create",
                "api/delete",
            )
        )


def test_fresh_interpreter_import_construction_and_mock_io_isolation(
    tmp_path: Path,
) -> None:
    script = (
        """
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
    raise AssertionError("Unexpected embedding side effect")
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
                 "phoenix.voice", "phoenix.models.ollama", "phoenix.models.registry",
                 "phoenix.models.lifecycle")
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
from phoenix.models.embeddings import (
    EmbeddingRequest, EmbeddingResponse, EmbeddingProvider,
)
from phoenix.models.ollama_embeddings import OllamaEmbeddingProvider
"""
        + inspect.getsource(FakeProvider)
        + """
req = EmbeddingRequest(model_id="model", inputs=("text",))
EmbeddingResponse(operation_id=req.operation_id, provider_id="fixture",
                  model_id="model", embeddings=((0.5,),), dimensions=1)
fake = FakeProvider("fixture")
default = OllamaEmbeddingProvider()
calls = []
def handler(req):
    calls.append(req.url.path)
    return httpx.Response(200, json={"embeddings": [[0.5]]})
backend = OllamaEmbeddingProvider(transport=httpx.MockTransport(handler))
assert not calls
async def run():
    asyncio.get_running_loop().create_task = forbidden
    await fake.embed(req)
    assert (await backend.embed(req)).dimensions == 1
    assert calls == ["/api/embed"]
    await backend.aclose()
    await default.aclose()
    assert not list(Path.cwd().iterdir())
    print("Embedding isolation: PASS")
try:
    loop.run_until_complete(run())
finally:
    loop.close()
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
    assert result.stdout.strip() == "Embedding isolation: PASS"
