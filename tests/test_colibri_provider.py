"""PHX-015 deterministic HTTP, SSE, cancellation and optional-runtime evidence."""

import ast
import asyncio
import inspect
import json
import subprocess
import sys
from collections.abc import AsyncIterator, Awaitable
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

import httpx
import pytest
from pydantic import ValidationError

from phoenix.models import colibri
from phoenix.models.colibri import ColibriProvider
from phoenix.models.lifecycle import ModelLifecycleManager
from phoenix.models.provider import (
    CancellationStatus,
    GenerationChunk,
    GenerationRequest,
    MessageRole,
    ModelProvider,
    ModelProviderError,
    ProviderHealthStatus,
    ProviderUnavailableError,
)
from phoenix.models.registry import ModelCapabilityRegistry


def run(coroutine: Awaitable[None]) -> None:
    async def bounded() -> None:
        await asyncio.wait_for(coroutine, 5)

    asyncio.run(bounded())


def request(operation: str = "operation", **changes: object) -> GenerationRequest:
    return GenerationRequest.model_validate(
        {
            "operation_id": operation,
            "model_id": "served-model",
            "messages": [{"role": role, "text": role.value} for role in MessageRole],
            "temperature": 0,
            "max_output_tokens": 16,
            "stop_sequences": ["STOP", "END"],
            **changes,
        }
    )


def model(**changes: object) -> dict[str, object]:
    return {
        "id": "served-model",
        "object": "model",
        "created": 1,
        "owned_by": "colibri",
        **changes,
    }


def completion(**changes: object) -> dict[str, object]:
    return {
        "object": "chat.completion",
        "model": "served-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "PHOENIX_OK"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        **changes,
    }


def chunk(
    text: str = "part", finish: str | None = None, **changes: object
) -> dict[str, object]:
    return {
        "object": "chat.completion.chunk",
        "model": "served-model",
        "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": finish}],
        **changes,
    }


def sse(data: object) -> bytes:
    return ("data: " + json.dumps(data, ensure_ascii=False) + "\n\n").encode()


class ByteStream(httpx.AsyncByteStream):
    def __init__(self, parts: list[bytes]) -> None:
        self.parts = parts
        self.consumed = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for part in self.parts:
            self.consumed += 1
            yield part

    async def aclose(self) -> None:
        self.closed = True


def test_default_identity_construction_and_client_policy() -> None:
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(200)

    backend = ColibriProvider(transport=httpx.MockTransport(handler))
    assert isinstance(backend, ModelProvider) and backend.provider_id == "colibri"
    assert str(backend.base_url) == "http://127.0.0.1:8000/v1/"
    assert not calls and backend.active_operation_ids == ()
    assert backend._client.timeout.as_dict() == {
        "connect": 3,
        "read": 600,
        "write": 10,
        "pool": 5,
    }
    assert backend._operation_timeout == 1800 and backend._metadata_timeout == 10
    asyncio.run(backend.aclose())
    with patch("phoenix.models.colibri.httpx.AsyncClient") as factory:
        ColibriProvider()
        assert factory.call_args.kwargs["trust_env"] is False
        assert factory.call_args.kwargs["follow_redirects"] is False
        assert factory.call_args.kwargs.get("verify", True) is True


@pytest.mark.parametrize(
    "url,expected",
    [
        ("HTTP://LOCALHOST:8000/v1///", "http://localhost:8000/v1/"),
        ("https://example.test/proxy/v1", "https://example.test/proxy/v1/"),
        ("http://[::1]:8000/v1", "http://[::1]:8000/v1/"),
        ("http://localhost", "http://localhost/"),
    ],
)
def test_normalization(url: str, expected: str) -> None:
    backend = ColibriProvider(url)
    assert str(backend.base_url) == expected
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
def test_invalid_url_privacy(url: str) -> None:
    with pytest.raises(ValueError) as failure:
        ColibriProvider(url)
    assert url not in str(failure.value) and "private" not in str(failure.value)


@pytest.mark.parametrize(
    "key", ["", " ", "bad\nkey", "bad\rkey", "bad key", "é", 1, True]
)
def test_invalid_keys(key: object) -> None:
    with pytest.raises(ValueError) as failure:
        ColibriProvider(api_key=key)  # type: ignore[arg-type]
    assert "bad" not in str(failure.value)


@pytest.mark.parametrize("key", [None, "fixture-key"])
def test_auth_headers_and_repr_privacy(key: str | None) -> None:
    async def exercise() -> None:
        calls: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            calls.append(req)
            if key is None:
                assert "authorization" not in req.headers
            else:
                assert req.headers["authorization"] == "Bearer " + key
            if req.url.path.endswith("/models"):
                return httpx.Response(200, json={"object": "list", "data": []})
            if "/models/" in req.url.path:
                return httpx.Response(200, json=model())
            return httpx.Response(200, json=completion())

        async with ColibriProvider(
            api_key=key, transport=httpx.MockTransport(handler)
        ) as backend:
            assert "fixture-key" not in repr(backend) + repr(vars(backend))
            assert (await backend.health()).status is ProviderHealthStatus.HEALTHY
            await backend.capabilities("served-model")
            await backend.generate(request())
            assert len(calls) == 3

    run(exercise())


@pytest.mark.parametrize(
    "phase", ["connect", "read", "write", "pool", "operation", "metadata", "cleanup"]
)
@pytest.mark.parametrize("value", [None, True, 0, -1, float("inf"), float("nan")])
def test_timeout_validation(phase: str, value: object) -> None:
    options: dict[str, object] = {}
    if phase in ("operation", "metadata", "cleanup"):
        options[phase + "_timeout"] = value
    else:
        options["timeout"] = httpx.Timeout(1, **{phase: value})  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ColibriProvider(**options)  # type: ignore[arg-type]


@pytest.mark.parametrize("models", [[], [model()]])
def test_health_valid_lists_and_empty_list(models: list[dict[str, object]]) -> None:
    async def exercise() -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            assert req.method == "GET" and req.url.path == "/v1/models"
            return httpx.Response(200, json={"object": "list", "data": models})

        async with ColibriProvider(transport=httpx.MockTransport(handler)) as backend:
            health = await backend.health()
            assert (
                health.status is ProviderHealthStatus.HEALTHY
                and health.provider_id == "colibri"
            )
            assert health.checked_at.utcoffset() is not None
            assert backend.active_operation_ids == ()

    run(exercise())


@pytest.mark.parametrize(
    "data",
    [
        None,
        {},
        [],
        {"object": "list"},
        {"object": "list", "data": None},
        {"object": "other", "data": []},
        {"object": "list", "data": [{}]},
        {"object": "list", "data": [model(id=1)]},
        {"object": "list", "data": [model(id="")]},
        {"object": "list", "data": [model(id=" ")]},
        {"error": "private"},
    ],
)
def test_invalid_health_is_degraded(data: object) -> None:
    async def exercise() -> None:
        async with ColibriProvider(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json=data))
        ) as backend:
            health = await backend.health()
            assert health.status is ProviderHealthStatus.DEGRADED
            assert "private" not in (health.detail or "")

    run(exercise())


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError,
        httpx.ReadError,
        httpx.WriteError,
        httpx.ProxyError,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.WriteTimeout,
        httpx.PoolTimeout,
    ],
)
def test_transport_failures_health_and_generation(error: type[httpx.HTTPError]) -> None:
    async def exercise() -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            raise error("private URL body fixture-key")

        async with ColibriProvider(transport=httpx.MockTransport(handler)) as backend:
            assert (await backend.health()).status is ProviderHealthStatus.UNAVAILABLE
            for call in (
                backend.generate(request()),
                backend.capabilities("served-model"),
            ):
                with pytest.raises(ProviderUnavailableError) as failure:
                    await call
                assert "private" not in str(failure.value)
            assert backend.active_operation_ids == ()

    run(exercise())


@pytest.mark.parametrize("status", [301, 302, 307, 400, 401, 404, 429, 500])
def test_http_error_and_redirect_policy(status: int) -> None:
    async def exercise() -> None:
        calls: list[str] = []

        def handler(req: httpx.Request) -> httpx.Response:
            calls.append(str(req.url))
            return httpx.Response(
                status,
                text="private prompt fixture-key",
                headers={"location": "https://remote.test"},
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=True
        ) as client:
            backend = ColibriProvider(client=client, api_key="fixture-key")
            assert (await backend.health()).status is ProviderHealthStatus.DEGRADED
            for call in (
                backend.generate(request()),
                backend.capabilities("served-model"),
            ):
                with pytest.raises(ModelProviderError) as failure:
                    await call
                assert not isinstance(failure.value, ProviderUnavailableError)
                assert all(
                    secret not in str(failure.value)
                    for secret in ("private", "fixture-key", "remote")
                )
            with pytest.raises(ModelProviderError):
                await anext(backend.stream(request()))
            assert len(calls) == 4 and backend.active_operation_ids == ()
            await backend.aclose()
            assert not client.is_closed

    run(exercise())


@pytest.mark.parametrize(
    "identity",
    [
        "served-model",
        "org/model?param#frag",
        "../models",
        ".",
        "..",
        "percent%20 space",
    ],
)
def test_model_endpoint_encoding_and_conservative_capabilities(identity: str) -> None:
    async def exercise() -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            expected = quote(identity, safe="").replace(".", "%2E")
            assert req.url.raw_path == ("/v1/models/" + expected).encode()
            assert not req.url.query
            return httpx.Response(
                200,
                json=model(id=identity, context_length=999999, supports_vision=True),
            )

        async with ColibriProvider(transport=httpx.MockTransport(handler)) as backend:
            caps = await backend.capabilities(identity)
            assert caps.provider_id == "colibri" and caps.model_id == identity
            assert caps.supports_streaming and caps.supports_cancellation
            assert (
                not caps.supports_structured_output
                and not caps.supports_tool_use
                and not caps.supports_vision
            )
            assert caps.context_limit_tokens is None

    run(exercise())


@pytest.mark.parametrize(
    "data",
    [
        {},
        model(id="other"),
        model(id="served-model:latest"),
        model(id=" served-model "),
        model(id=None),
        model(object="other"),
    ],
)
def test_invalid_capability_records(data: object) -> None:
    async def exercise() -> None:
        async with ColibriProvider(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json=data))
        ) as backend:
            with pytest.raises(ModelProviderError):
                await backend.capabilities("served-model")

    run(exercise())


@pytest.mark.parametrize("identity", ["", " ", 1, None])
def test_invalid_capability_request_id(identity: object) -> None:
    async def exercise() -> None:
        async with ColibriProvider() as backend:
            with pytest.raises(ValidationError):
                await backend.capabilities(identity)  # type: ignore[arg-type]

    run(exercise())


@pytest.mark.parametrize("with_controls", [True, False])
def test_nonstream_request_response_and_metrics(with_controls: bool) -> None:
    async def exercise() -> None:
        req = (
            request()
            if with_controls
            else request(temperature=None, max_output_tokens=None, stop_sequences=())
        )

        def handler(received: httpx.Request) -> httpx.Response:
            expected: dict[str, object] = {
                "model": "served-model",
                "messages": [
                    {"role": role.value, "content": role.value} for role in MessageRole
                ],
                "stream": False,
            }
            if with_controls:
                expected.update(temperature=0, max_tokens=16, stop=["STOP", "END"])
            assert (
                received.method == "POST"
                and received.url.path == "/v1/chat/completions"
            )
            assert json.loads(received.content) == expected
            assert received.extensions["timeout"] == {
                "connect": 1,
                "read": 2,
                "write": 3,
                "pool": 4,
            }
            return httpx.Response(
                200,
                json=completion(
                    colibri={"tok_s": 5, "private": "body"}, metrics={"duration_ms": 10}
                ),
            )

        async with ColibriProvider(
            transport=httpx.MockTransport(handler),
            timeout=httpx.Timeout(connect=1, read=2, write=3, pool=4),
        ) as backend:
            result = await backend.generate(req)
            assert (result.operation_id, result.provider_id, result.model_id) == (
                req.operation_id,
                "colibri",
                req.model_id,
            )
            assert result.text == "PHOENIX_OK" and result.finish_reason == "stop"
            assert result.usage and result.usage.model_dump() == {
                "input_tokens": 3,
                "output_tokens": 2,
                "total_tokens": 5,
            }
            assert result.metrics is None and "private" not in result.model_dump_json()
            assert backend.active_operation_ids == ()

    run(exercise())


@pytest.mark.parametrize(
    "data,expected",
    [
        (completion(usage=None), (None, None, None)),
        (completion(usage={"prompt_tokens": 0, "completion_tokens": 2}), (0, 2, None)),
        (completion(usage={"total_tokens": 5}), (None, None, 5)),
    ],
)
def test_optional_usage_no_fabrication(
    data: object, expected: tuple[int | None, ...]
) -> None:
    async def exercise() -> None:
        async with ColibriProvider(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json=data))
        ) as backend:
            result = await backend.generate(request())
            actual = (
                tuple(result.usage.model_dump().values())
                if result.usage
                else (None, None, None)
            )
            assert actual == expected and result.metrics is None

    run(exercise())


def test_omitted_response_model_finish_and_usage() -> None:
    data = completion()
    data.pop("model")
    data.pop("usage")
    data["choices"] = [{"index": 0, "message": {"role": "assistant", "content": ""}}]

    async def exercise() -> None:
        async with ColibriProvider(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json=data))
        ) as backend:
            result = await backend.generate(request())
            assert (
                result.text == ""
                and result.finish_reason is None
                and result.usage is None
            )
            assert result.model_id == "served-model"

    run(exercise())


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {},
        {"error": "private"},
        completion(model="other"),
        completion(model="served-model:latest"),
        completion(model=None),
        completion(object="other"),
        completion(choices=[]),
        completion(choices=[{}, {}]),
        completion(choices=[{}]),
        completion(
            choices=[
                {"index": True, "message": {"role": "assistant", "content": "private"}}
            ]
        ),
        completion(
            choices=[
                {"index": 1, "message": {"role": "assistant", "content": "private"}}
            ]
        ),
        completion(
            choices=[{"index": 0, "message": {"role": "user", "content": "private"}}]
        ),
        completion(
            choices=[{"index": 0, "message": {"role": "assistant", "content": None}}]
        ),
        completion(
            choices=[{"index": 0, "message": {"role": "assistant", "content": []}}]
        ),
        completion(
            choices=[
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "private"},
                    "finish_reason": 1,
                }
            ]
        ),
        completion(usage=[]),
        completion(usage={"prompt_tokens": -1}),
        completion(usage={"completion_tokens": True}),
        completion(usage={"total_tokens": "5"}),
        completion(usage={"total_tokens": 1.5}),
    ],
)
def test_untrusted_completion_fails_safely_and_cleans(data: object) -> None:
    async def exercise() -> None:
        async with ColibriProvider(
            "https://private.test/v1",
            api_key="fixture-key",
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json=data)),
        ) as backend:
            with pytest.raises(ModelProviderError) as failure:
                await backend.generate(
                    request(messages=[{"role": "user", "text": "private prompt"}])
                )
            assert all(
                secret not in str(failure.value)
                for secret in ("private", "fixture-key", "https", "prompt")
            )
            assert backend.active_operation_ids == ()

    run(exercise())


@pytest.mark.parametrize("body", [b"private-invalid-json", b"{", b"\xff"])
def test_malformed_json(body: bytes) -> None:
    async def exercise() -> None:
        async with ColibriProvider(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, content=body))
        ) as backend:
            assert (await backend.health()).status is ProviderHealthStatus.DEGRADED
            with pytest.raises(ModelProviderError):
                await backend.generate(request())
            assert backend.active_operation_ids == ()

    run(exercise())


def test_stream_is_incremental_with_final_metadata_and_extensions() -> None:
    async def exercise() -> None:
        source = ByteStream(
            [
                b": keepalive\n\n",
                sse(chunk("first")),
                sse(chunk("second", "stop", usage={"prompt_tokens": 3})),
                sse({"colibri": {"tok_s": 20, "expert_map": "private"}}),
                sse(chunk(choices=[], usage={"completion_tokens": 2})),
                b"data: [DONE]\n\n",
            ]
        )

        def handler(received: httpx.Request) -> httpx.Response:
            body = json.loads(received.content)
            assert body["stream"] is True and body["stop"] == ["STOP", "END"]
            assert "stream_options" not in body and "tools" not in body
            assert received.headers["authorization"] == "Bearer fixture-key"
            return httpx.Response(200, stream=source)

        async with ColibriProvider(
            transport=httpx.MockTransport(handler), api_key="fixture-key"
        ) as backend:
            iterator = backend.stream(request())
            first = await anext(iterator)
            assert first.text == "first" and not first.is_final
            assert source.consumed == 2 < len(source.parts)
            assert backend.active_operation_ids == ("operation",)
            chunks = [first] + [item async for item in iterator]
            assert "".join(item.text for item in chunks) == "firstsecond"
            assert sum(item.is_final for item in chunks) == 1 and chunks[-1].is_final
            assert all(
                (item.operation_id, item.provider_id, item.model_id)
                == ("operation", "colibri", "served-model")
                for item in chunks
            )
            final = chunks[-1]
            assert final.finish_reason == "stop" and final.usage
            assert final.usage.input_tokens == 3 and final.usage.output_tokens == 2
            assert final.usage.total_tokens is None and final.metrics is None
            assert "private" not in final.model_dump_json()
            assert source.closed and backend.active_operation_ids == ()

    run(exercise())


@pytest.mark.parametrize("newline", [b"\n", b"\r\n", b"\r"])
def test_sse_fragmented_multiline_utf8_role_and_null_content(newline: bytes) -> None:
    async def exercise() -> None:
        encoded = sse(chunk("é")).replace(b"\n", newline)
        split = encoded.index("é".encode()) + 1
        source = ByteStream(
            [
                b"da",
                b'ta: {"object":"chat.completion.chunk",' + newline,
                b'data: "choices":[{"index":0,"delta":'
                b'{"role":"assistant","content":null}}]}' + newline * 2,
                encoded[:split],
                encoded[split:],
                b"data:[DONE]" + newline,
                newline,
            ]
        )
        async with ColibriProvider(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, stream=source)
            )
        ) as backend:
            chunks = [item async for item in backend.stream(request())]
            assert "".join(item.text for item in chunks) == "é"
            assert chunks[-1].is_final and source.closed

    run(exercise())


@pytest.mark.parametrize(
    "parts",
    [
        [],
        [sse(chunk())],
        [sse(chunk(finish="stop"))],
        [b"data: [DONE]\n\n"],
        [b"data: private-invalid\n\n"],
        [sse(chunk()).replace(b"part", b"\xff")],
        [b"event: private\n\n"],
        [b"private\n"],
        [b"data: {}\n\n"],
        [sse(chunk(model="other"))],
        [sse(chunk(choices=[]))],
        [sse(chunk(choices=[{}, {}]))],
        [sse(chunk(choices=[{"index": 0, "delta": {"content": 1}}]))],
        [sse(chunk(choices=[{"index": True, "delta": {}}]))],
        [sse(chunk(choices=[{"index": 0, "delta": {"role": "user"}}]))],
        [sse(chunk(usage={"total_tokens": -1}))],
        [sse(chunk(usage={"prompt_tokens": True}))],
        [sse({"colibri": "private"})],
        [sse({"colibri": {}, "error": "private"})],
        [sse({"colibri": {}, "text": "private"})],
        [sse(chunk(finish="stop")), sse(chunk("after"))],
        [b"data: {}\n"],
        [sse({"object": "colibri-unknown", "data": {}})],
    ],
)
def test_invalid_streams_have_no_final_and_cleanup(parts: list[bytes]) -> None:
    async def exercise() -> None:
        source = ByteStream(parts)
        async with ColibriProvider(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, stream=source)
            )
        ) as backend:
            seen: list[GenerationChunk] = []
            with pytest.raises(ModelProviderError) as failure:
                async for item in backend.stream(request()):
                    seen.append(item)
            assert "private" not in str(failure.value)
            assert not any(item.is_final for item in seen)
            assert source.closed and backend.active_operation_ids == ()

    run(exercise())


def test_telemetry_is_never_terminal_evidence() -> None:
    async def exercise() -> None:
        source = ByteStream([sse(chunk("first")), sse({"colibri": {}})])
        async with ColibriProvider(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, stream=source)
            )
        ) as backend:
            iterator = backend.stream(request())
            assert not (await anext(iterator)).is_final
            with pytest.raises(ModelProviderError, match="terminal"):
                await anext(iterator)
            assert source.closed and backend.active_operation_ids == ()

    run(exercise())


def test_generate_cancel_scoped_duplicate_rejection_and_id_reuse() -> None:
    async def exercise() -> None:
        entered = {key: asyncio.Event() for key in ("a", "b")}
        release = {key: asyncio.Event() for key in ("a", "b")}

        async def handler(req: httpx.Request) -> httpx.Response:
            key = json.loads(req.content)["messages"][0]["content"]
            entered[key].set()
            await release[key].wait()
            return httpx.Response(200, json=completion())

        async with ColibriProvider(transport=httpx.MockTransport(handler)) as backend:
            req_a = request("a", messages=[{"role": "user", "text": "a"}])
            req_b = request("b", messages=[{"role": "user", "text": "b"}])
            task_a = asyncio.create_task(backend.generate(req_a))
            task_b = asyncio.create_task(backend.generate(req_b))
            await asyncio.gather(*(event.wait() for event in entered.values()))
            with pytest.raises(ModelProviderError, match="duplicate"):
                await backend.generate(req_a)
            assert set(backend.active_operation_ids) == {"a", "b"}
            cancelled = await backend.cancel("a")
            assert (
                cancelled.status is CancellationStatus.ACCEPTED
                and cancelled.provider_id == "colibri"
                and cancelled.operation_id == "a"
            )
            with pytest.raises(asyncio.CancelledError):
                await task_a
            assert backend.active_operation_ids == ("b",) and not task_b.done()
            release["b"].set()
            assert (await task_b).text == "PHOENIX_OK"
            assert (
                await backend.cancel("unknown")
            ).status is CancellationStatus.NOT_FOUND
            release["a"].set()
            await backend.generate(req_a)
            assert backend.active_operation_ids == ()

    run(exercise())


@pytest.mark.parametrize(
    "action", ["cancel", "close", "timeout", "disconnect", "caller-cancel"]
)
def test_blocked_stream_cancellation_failure_and_cleanup(action: str) -> None:
    async def exercise() -> None:
        entered = asyncio.Event()

        class Blocked(ByteStream):
            async def __aiter__(self) -> AsyncIterator[bytes]:
                yield sse(chunk("first"))
                entered.set()
                if action == "disconnect":
                    raise httpx.ReadError("private")
                await asyncio.Event().wait()

        source = Blocked([])
        backend = ColibriProvider(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, stream=source)
            ),
            operation_timeout=0.1 if action == "timeout" else 1800,
        )
        iterator = backend.stream(request())
        assert (await anext(iterator)).text == "first"
        task = asyncio.create_task(anext(iterator))
        await entered.wait()
        if action == "cancel":
            assert (
                await backend.cancel("operation")
            ).status is CancellationStatus.ACCEPTED
        elif action == "close":
            await backend.aclose()
        elif action == "caller-cancel":
            task.cancel()
        expected = (
            ProviderUnavailableError
            if action in ("timeout", "disconnect")
            else asyncio.CancelledError
        )
        with pytest.raises(expected):
            await task
        assert source.closed and backend.active_operation_ids == ()
        await backend.aclose()

    run(exercise())


@pytest.mark.parametrize(
    "action", ["cancel", "provider-close", "iterator-close", "consumer-error"]
)
def test_suspended_stream_cleanup_does_not_cancel_consumer(action: str) -> None:
    async def exercise() -> None:
        source = ByteStream([sse(chunk()), sse(chunk()), b"data: [DONE]\n\n"])
        backend = ColibriProvider(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, stream=source)
            )
        )
        iterator = backend.stream(request())
        await anext(iterator)
        if action == "cancel":
            assert (
                await backend.cancel("operation")
            ).status is CancellationStatus.ACCEPTED
            with pytest.raises(asyncio.CancelledError):
                await anext(iterator)
        elif action == "provider-close":
            await backend.aclose()
            with pytest.raises(asyncio.CancelledError):
                await anext(iterator)
        elif action == "consumer-error":
            with pytest.raises(ValueError):
                try:
                    raise ValueError("consumer failure")
                finally:
                    await iterator.aclose()
        else:
            await iterator.aclose()
        current = asyncio.current_task()
        assert current and not current.cancelling()
        assert source.closed and backend.active_operation_ids == ()
        await backend.aclose()

    run(exercise())


def test_concurrent_streams_and_cross_method_duplicate_ids() -> None:
    async def exercise() -> None:
        entered = {key: asyncio.Event() for key in ("a", "b")}
        release = {key: asyncio.Event() for key in ("a", "b")}

        class Independent(ByteStream):
            def __init__(self, key: str) -> None:
                super().__init__([])
                self.key = key

            async def __aiter__(self) -> AsyncIterator[bytes]:
                yield sse(chunk(self.key))
                entered[self.key].set()
                await release[self.key].wait()
                yield b"data: [DONE]\n\n"

        sources = {key: Independent(key) for key in ("a", "b")}

        def handler(req: httpx.Request) -> httpx.Response:
            key = json.loads(req.content)["messages"][0]["content"]
            return httpx.Response(200, stream=sources[key])

        async with ColibriProvider(transport=httpx.MockTransport(handler)) as backend:
            req_a = request("a", messages=[{"role": "user", "text": "a"}])
            req_b = request("b", messages=[{"role": "user", "text": "b"}])
            iter_a, iter_b = backend.stream(req_a), backend.stream(req_b)
            assert (await anext(iter_a)).text == "a"
            assert (await anext(iter_b)).text == "b"
            with pytest.raises(ModelProviderError, match="duplicate"):
                await backend.generate(req_a)
            duplicate = backend.stream(req_b)
            with pytest.raises(ModelProviderError, match="duplicate"):
                await anext(duplicate)
            task_a = asyncio.create_task(anext(iter_a))
            task_b = asyncio.create_task(anext(iter_b))
            await asyncio.gather(*(event.wait() for event in entered.values()))
            await backend.cancel("a")
            with pytest.raises(asyncio.CancelledError):
                await task_a
            assert sources["a"].closed and not sources["b"].closed
            assert backend.active_operation_ids == ("b",) and not task_b.done()
            release["b"].set()
            assert (await task_b).is_final
            await iter_b.aclose()
            assert sources["b"].closed and backend.active_operation_ids == ()

    run(exercise())


def test_stale_cancelled_iterator_cannot_unregister_reused_id() -> None:
    async def exercise() -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        source = ByteStream([sse(chunk()), b"data: [DONE]\n\n"])

        async def handler(req: httpx.Request) -> httpx.Response:
            if json.loads(req.content)["stream"]:
                return httpx.Response(200, stream=source)
            entered.set()
            await release.wait()
            return httpx.Response(200, json=completion())

        async with ColibriProvider(transport=httpx.MockTransport(handler)) as backend:
            iterator = backend.stream(request())
            await anext(iterator)
            await backend.cancel("operation")
            assert source.closed and backend.active_operation_ids == ()
            replacement = asyncio.create_task(backend.generate(request()))
            await entered.wait()
            with pytest.raises(asyncio.CancelledError):
                await anext(iterator)
            assert backend.active_operation_ids == ("operation",)
            assert not replacement.done()
            release.set()
            assert (await replacement).text == "PHOENIX_OK"
            assert backend.active_operation_ids == ()

    run(exercise())


def test_stream_deadline_between_chunks_does_not_cancel_consumer() -> None:
    async def exercise() -> None:
        source = ByteStream([sse(chunk()), sse(chunk()), b"data: [DONE]\n\n"])
        async with ColibriProvider(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, stream=source)
            ),
            operation_timeout=10,
        ) as backend:
            iterator = backend.stream(request())
            await anext(iterator)
            loop = asyncio.get_running_loop()
            clock = loop.time
            with patch.object(loop, "time", lambda: clock() + 20):
                with pytest.raises(ProviderUnavailableError):
                    await anext(iterator)
            current = asyncio.current_task()
            assert current and not current.cancelling()
            assert source.closed and backend.active_operation_ids == ()

    run(exercise())


def test_metadata_and_generation_overall_timeout_cleanup() -> None:
    async def exercise() -> None:
        async def handler(req: httpx.Request) -> httpx.Response:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async with ColibriProvider(
            transport=httpx.MockTransport(handler),
            metadata_timeout=0.01,
            operation_timeout=0.01,
        ) as backend:
            assert (await backend.health()).status is ProviderHealthStatus.UNAVAILABLE
            with pytest.raises(ProviderUnavailableError):
                await backend.generate(request())
            with pytest.raises(ProviderUnavailableError):
                await backend.capabilities("served-model")
            assert backend.active_operation_ids == ()

    run(exercise())


def test_provider_close_cancels_active_generation() -> None:
    async def exercise() -> None:
        entered = asyncio.Event()

        async def handler(req: httpx.Request) -> httpx.Response:
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        backend = ColibriProvider(transport=httpx.MockTransport(handler))
        task = asyncio.create_task(backend.generate(request()))
        await entered.wait()
        await backend.aclose()
        assert (
            task.cancelled()
            and backend.active_operation_ids == ()
            and backend._client.is_closed
        )
        await backend.aclose()
        with pytest.raises(ModelProviderError, match="closed"):
            await backend.generate(request())

    run(exercise())


def test_owned_borrowed_clients_and_exclusive_injection() -> None:
    async def exercise() -> None:
        async with httpx.AsyncClient() as client:
            with pytest.raises(ValueError):
                ColibriProvider(
                    client=client,
                    transport=httpx.MockTransport(lambda req: httpx.Response(200)),
                )
            backend = ColibriProvider(client=client)
            await backend.aclose()
            await backend.aclose()
            assert not client.is_closed
        backend = ColibriProvider()
        await backend.aclose()
        assert backend._client.is_closed

    run(exercise())


def test_cleanup_timeout_is_explicit_and_bounded() -> None:
    class HangingTransport(httpx.AsyncBaseTransport):
        async def aclose(self) -> None:
            await asyncio.Event().wait()

    async def exercise() -> None:
        backend = ColibriProvider(transport=HangingTransport(), cleanup_timeout=0.01)
        with pytest.raises(ModelProviderError, match="cleanup"):
            await backend.aclose()
        assert backend.active_operation_ids == ()

    run(exercise())


def test_registry_and_lifecycle_neutral_interoperability() -> None:
    async def exercise() -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json=model() if req.method == "GET" else completion()
            )

        async with ColibriProvider(transport=httpx.MockTransport(handler)) as backend:
            registry = ModelCapabilityRegistry()
            result = await registry.refresh(backend, "served-model")
            assert (
                result.provider_id == "colibri"
                and registry.get("colibri", "served-model") == result
            )
            manager = ModelLifecycleManager()
            req = request()
            async with await manager.acquire_heavy(
                provider_id=backend.provider_id,
                model_id=req.model_id,
                operation_id=req.operation_id,
            ):
                assert manager.active_count == 1
                assert (await backend.generate(req)).provider_id == "colibri"
            assert manager.active_count == 0

    run(exercise())


def test_request_validation_before_io_and_cross_loop_use() -> None:
    async def exercise() -> None:
        calls: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            calls.append(req)
            return httpx.Response(200, json=completion())

        async with ColibriProvider(transport=httpx.MockTransport(handler)) as backend:
            with pytest.raises(ModelProviderError):
                await backend.generate(request().model_copy(update={"temperature": -1}))
            assert not calls and backend.active_operation_ids == ()

    run(exercise())
    backend = ColibriProvider(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json=completion())
        )
    )
    asyncio.run(backend.generate(request()))
    with pytest.raises(ModelProviderError, match="event loop"):
        asyncio.run(backend.generate(request()))
    asyncio.run(backend.aclose())


def test_module_dependency_scope() -> None:
    allowed = {
        "asyncio",
        "json",
        "math",
        "collections.abc",
        "dataclasses",
        "typing",
        "urllib.parse",
        "httpx",
        "pydantic",
        "phoenix.models.provider",
    }
    source = inspect.getsource(colibri)
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            assert all(item.name in allowed for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module in allowed
    assert not any(
        endpoint in source
        for endpoint in ("/shutdown", "/unload", "/pull", "api/chat", "embeddings")
    )


def test_fresh_interpreter_import_construction_mock_io_isolation(
    tmp_path: Path,
) -> None:
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
    raise AssertionError("Unexpected Colibri side effect")
def audit(event, args):
    if event == "open" and args[2] & (
        os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    ):
        forbidden()
class BlockImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        roots = ("fastapi", "sqlalchemy", "alembic", "psutil", "ollama", "colibri",
                 "openai", "anthropic", "phoenix.core", "phoenix.api",
                 "phoenix.persistence", "phoenix.agents", "phoenix.tools",
                 "phoenix.permissions", "phoenix.voice", "phoenix.models.ollama",
                 "phoenix.models.registry", "phoenix.models.lifecycle",
                 "phoenix.models.embeddings")
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
from phoenix.models.colibri import ColibriProvider
from phoenix.models.provider import GenerationRequest, ModelMessage, MessageRole
default = ColibriProvider()
calls = []
def handler(req):
    calls.append(req.url.path)
    if req.url.path == '/v1/models':
        return httpx.Response(200, json={'object': 'list', 'data': []})
    if req.method == 'GET':
        return httpx.Response(200, json={'object': 'model', 'id': 'served-model'})
    record = {'object': 'chat.completion', 'choices': [{'index': 0,
              'message': {'role': 'assistant', 'content': 'fixture'}}]}
    return httpx.Response(200, json=record)
backend = ColibriProvider(transport=httpx.MockTransport(handler))
assert not calls
async def exercise():
    asyncio.get_running_loop().create_task = forbidden
    await backend.health()
    await backend.capabilities('served-model')
    request = GenerationRequest(model_id='served-model', messages=(
        ModelMessage(role=MessageRole.USER, text='fixture'),))
    await backend.generate(request)
    await backend.aclose()
    await default.aclose()
    assert calls == ['/v1/models', '/v1/models/served-model', '/v1/chat/completions']
    assert not list(Path.cwd().iterdir())
    print('Colibri isolation: PASS')
try:
    loop.run_until_complete(exercise())
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
    assert result.stdout.strip() == "Colibri isolation: PASS"
