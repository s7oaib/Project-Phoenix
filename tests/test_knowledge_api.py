"""PHX-110 knowledge routes and cited missions over the REST API (ADR-131-133).

Real components through Starlette's TestClient: the composition root, PHX-101
middleware, the body cap, MissionService, PhoenixRuntime, KnowledgeService and
alembic-migrated SQLite. Only the model and the embeddings are scripted. No
network, Ollama or GPU is used; an autouse guard fails any httpx send or
outbound socket connection.
"""

import json
import re
import secrets
import socket
import time
from collections.abc import AsyncIterator, Callable, Iterator
from hashlib import md5
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from phoenix.api.body_limit import MAX_BODY_BYTES, MAX_UPLOAD_BYTES
from phoenix.api.security import LocalApiSecurity
from phoenix.api.server import ProviderSet, ServerConfig, build_server_app
from phoenix.core.config import AppSettings
from phoenix.models.embeddings import (
    EmbeddingProvider,
    EmbeddingRequest,
    EmbeddingResponse,
)
from phoenix.models.provider import (
    CancellationResult,
    CancellationStatus,
    GenerationChunk,
    GenerationRequest,
    GenerationResponse,
    ModelCapabilities,
    ModelProvider,
    ModelProviderError,
    ProviderHealth,
    ProviderHealthStatus,
    TokenUsage,
)

TOKEN = secrets.token_urlsafe(32)
PORT = 48767  # Host-header identity for in-process clients; nothing binds it
SECURITY = LocalApiSecurity(TOKEN, host="127.0.0.1", port=PORT)
FAST = ServerConfig(probe_timeout_seconds=0.5, startup_timeout_seconds=5)
PASS = '{"checks":[{"criterion":"Correct","passed":true,"evidence_summary":"Fine."}]}'
DOC_SENTINEL = "SENTINEL-DOC-71"
BUDGET = f"The PELICAN project budget is 4000 euros for the pilot. {DOC_SENTINEL}\n"
OBJECTIVE = "Find the PELICAN project budget in the uploaded documents."
UNKNOWN = "aaaaaaaa-0000-4000-8000-00000000000b"
SETTLED = {"completed", "failed", "cancelled", "waiting_permission", "superseded"}
DIM = 64
ROUTES = (
    ("POST", "/files/upload?filename=a.txt"),
    ("GET", "/files"),
    ("DELETE", f"/files/{UNKNOWN}"),
    ("POST", "/memory"),
    ("GET", "/memory"),
    ("GET", "/memory/search?q=x"),
    ("DELETE", f"/memory/{UNKNOWN}"),
)


def vector(text: str) -> tuple[float, ...]:
    values = [0.0] * DIM
    values[-1] = 0.05
    for word in re.findall(r"[a-z0-9]+", text.casefold()):
        if len(word) >= 4:
            values[int(md5(word.encode()).hexdigest(), 16) % (DIM - 1)] += 1.0
    return tuple(values)


class Embeddings(EmbeddingProvider):
    def __init__(self) -> None:
        super().__init__("ollama")
        self.calls = 0

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        self.calls += 1
        return EmbeddingResponse(
            operation_id=request.operation_id,
            provider_id=self.provider_id,
            model_id=request.model_id,
            embeddings=tuple(vector(text) for text in request.inputs),
            dimensions=DIM,
        )


def role_of(request: GenerationRequest) -> str:
    return request.messages[0].text.splitlines()[0].removeprefix("PHOENIX ROLE: ")


def items_of(request: GenerationRequest) -> list[dict[str, Any]]:
    data = request.messages[1].text.partition("PHOENIX_BOUNDARY_JSON:")[2]
    items: list[dict[str, Any]] = json.loads(data)["items"]
    return items


def cite_budget(request: GenerationRequest) -> str:
    marker = next(
        item["content_id"]
        for item in items_of(request)
        if item["content_id"].startswith("[") and "4000" in item["text"]
    )
    return f"The PELICAN project budget is 4000 euros {marker}."


def repeat_s1(request: GenerationRequest) -> str:
    text: str = next(i["text"] for i in items_of(request) if i["content_id"] == "s1")
    return text


class ScriptedProvider(ModelProvider):
    def __init__(self) -> None:
        super().__init__("ollama")
        self.replies: dict[str, str | Callable[[GenerationRequest], str]] = {
            "triage": '{"path": "direct"}',
            "direct": "Hello.",
            "planner": json.dumps({"steps": [{"objective": OBJECTIVE}]}),
            "assignment": json.dumps(
                {"assignments": [{"step_id": "s1", "domain": "knowledge_rag"}]}
            ),
            "knowledge_answer": cite_budget,
            "knowledge_verifier": PASS,
            "final_answer": repeat_s1,
            "final_verifier": PASS,
        }

    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider_id=self.provider_id, status=ProviderHealthStatus.HEALTHY
        )

    async def capabilities(self, model_id: str) -> ModelCapabilities:
        return ModelCapabilities(
            provider_id="ollama",
            model_id=model_id,
            supports_streaming=True,
            supports_structured_output=False,
            supports_tool_use=True,
            supports_vision=False,
            supports_cancellation=True,
            context_limit_tokens=16384,
        )

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        reply = self.replies[role_of(request)]
        output = reply(request) if callable(reply) else reply
        return GenerationResponse(
            operation_id=request.operation_id,
            provider_id=self.provider_id,
            model_id=request.model_id,
            text=output,
            finish_reason="stop",
            usage=TokenUsage(input_tokens=5, output_tokens=3),
        )

    async def _no_stream(self) -> AsyncIterator[GenerationChunk]:
        raise ModelProviderError("streaming is not scripted")
        yield  # pragma: no cover

    def stream(self, request: GenerationRequest) -> AsyncIterator[GenerationChunk]:
        return self._no_stream()

    async def cancel(self, operation_id: str) -> CancellationResult:
        return CancellationResult(
            operation_id=operation_id,
            provider_id=self.provider_id,
            status=CancellationStatus.ACCEPTED,
        )


class Backend:
    def __init__(self, tmp_path: Path, *, embeddings: bool = True) -> None:
        self.settings = AppSettings(data_dir=tmp_path / "data", _env_file=None)  # type: ignore[call-arg]
        self.provider = ScriptedProvider()
        self.embeddings = Embeddings() if embeddings else None
        self.bodies: list[str] = []

    def client(self, *, token: str | None = TOKEN) -> TestClient:
        provider, embedding = self.provider, self.embeddings
        app = build_server_app(
            self.settings,
            security=SECURITY,
            config=FAST,
            provider_factory=lambda config: ProviderSet(
                providers=(provider,), embedding=embedding
            ),
        )
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        client = TestClient(app, base_url=f"http://127.0.0.1:{PORT}", headers=headers)

        def record(response: httpx.Response) -> None:
            response.read()
            self.bodies.append(response.text)

        client.event_hooks = {"request": [], "response": [record]}
        return client


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse_send(*args: object, **kwargs: object) -> None:
        raise AssertionError("httpx network send attempted")

    def refuse_connect(*args: object, **kwargs: object) -> None:
        raise AssertionError("outbound socket connection attempted")

    monkeypatch.setattr(httpx.AsyncClient, "send", refuse_send)
    monkeypatch.setattr(socket, "create_connection", refuse_connect)


@pytest.fixture
def backend(tmp_path: Path) -> Backend:
    return Backend(tmp_path)


@pytest.fixture
def client(backend: Backend) -> Iterator[TestClient]:
    with backend.client() as client:
        yield client


def error_code(response: httpx.Response) -> str:
    body = response.json()
    assert set(body) == {"error"}
    code: str = body["error"]["code"]
    return code


def upload(
    client: TestClient, name: str, data: bytes, content_type: str = "text/plain"
) -> httpx.Response:
    response: httpx.Response = client.post(
        "/files/upload",
        params={"filename": name},
        content=data,
        headers={"Content-Type": content_type},
    )
    return response


def wait_for(client: TestClient, mission_id: str, timeout: float = 20) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        body = client.get(f"/missions/{mission_id}").json()
        if body["status"] in SETTLED:
            return body
        assert time.monotonic() < deadline, body["status"]
        time.sleep(0.01)


# ---- authentication


def test_every_knowledge_route_requires_the_token(backend: Backend) -> None:
    with backend.client(token=None) as anonymous:
        for method, path in ROUTES:
            response = anonymous.request(method, path, content=b"x")
            assert response.status_code == 401, (method, path)
    with backend.client() as client:
        assert client.get("/files").json()["items"] == []
        assert client.get("/memory").json()["items"] == []
    assert backend.embeddings is not None and backend.embeddings.calls == 0


# ---- uploads


def test_upload_lists_and_deletes_a_document(client: TestClient) -> None:
    response = upload(client, "budget.txt", BUDGET.encode())
    assert response.status_code == 201, response.text
    document = response.json()
    assert set(document) == {
        "document_id",
        "filename",
        "media_type",
        "chunk_count",
        "indexed_at",
    }
    assert document["filename"] == "budget.txt" and document["chunk_count"] == 1
    assert DOC_SENTINEL not in response.text
    listed = client.get("/files").json()
    assert [item["document_id"] for item in listed["items"]] == [
        document["document_id"]
    ]
    assert client.delete(f"/files/{document['document_id']}").status_code == 204
    missing = client.delete(f"/files/{document['document_id']}")
    assert missing.status_code == 404 and error_code(missing) == "not_found"
    assert client.get("/files").json()["total"] == 0
    assert client.delete("/files/not-a-uuid").status_code == 422


@pytest.mark.parametrize(
    ("name", "data", "content_type", "status", "code"),
    [
        ("tool.exe", b"MZ", "application/octet-stream", 415, "unsupported_format"),
        ("notes.txt", b"hello", "application/pdf", 415, "media_type_mismatch"),
        ("report.pdf", b"not a pdf", "application/pdf", 422, "invalid_upload"),
        ("notes.txt", b"   \n", "text/plain", 422, "invalid_upload"),
        ("bad/name.txt", b"text", "text/plain", 422, "invalid_upload"),
        (
            "keys.txt",
            b"deploy password: hunter2222",
            "text/plain",
            422,
            "secret_rejected",
        ),
    ],
)
def test_invalid_uploads_are_refused_and_leave_nothing(
    backend: Backend,
    client: TestClient,
    name: str,
    data: bytes,
    content_type: str,
    status: int,
    code: str,
) -> None:
    response = upload(client, name, data, content_type)
    assert response.status_code == status, response.text
    assert error_code(response) == code
    assert client.get("/files").json()["total"] == 0
    for body in backend.bodies:
        assert "hunter" not in body and "bad/name" not in body


def test_upload_requires_a_filename_and_a_body(client: TestClient) -> None:
    assert client.post("/files/upload", content=b"text").status_code == 422
    empty = client.post("/files/upload", params={"filename": "a.txt"}, content=b"")
    assert empty.status_code == 422 and error_code(empty) == "invalid_upload"


def test_upload_has_its_own_body_cap(client: TestClient) -> None:
    large = ("PELICAN budget line\n" * (MAX_BODY_BYTES // 10)).encode()
    assert MAX_BODY_BYTES < len(large) < MAX_UPLOAD_BYTES
    accepted = upload(client, "large.txt", large)
    assert accepted.status_code == 201, accepted.text
    # Every other route keeps the 64 KiB cap.
    chat = client.post("/chat", content=large)
    assert chat.status_code == 413 and error_code(chat) == "payload_too_large"
    too_large = upload(client, "huge.txt", b"a" * (MAX_UPLOAD_BYTES + 1))
    assert too_large.status_code == 413
    assert error_code(too_large) == "payload_too_large"


def test_without_embeddings_knowledge_is_read_only(tmp_path: Path) -> None:
    backend = Backend(tmp_path, embeddings=False)
    with backend.client() as client:
        refused = upload(client, "budget.txt", BUDGET.encode())
        assert refused.status_code == 503
        assert error_code(refused) == "knowledge_unavailable"
        memory = client.post("/memory", json={"content": "I like tea."})
        assert error_code(memory) == "knowledge_unavailable"
        assert client.get("/files").json()["total"] == 0


# ---- memory


def test_memory_is_user_visible_searchable_and_deletable(client: TestClient) -> None:
    created = client.post(
        "/memory", json={"content": "The PELICAN pilot uses metric units."}
    )
    assert created.status_code == 201, created.text
    memory = created.json()
    assert memory["scope"] == "user" and memory["memory_type"] == "user"
    project = client.post(
        "/memory",
        json={"content": "PELICAN ships in May.", "memory_type": "project"},
    )
    assert project.status_code == 201
    listed = client.get("/memory").json()
    assert [m["memory_id"] for m in listed["items"]] == [
        memory["memory_id"],
        project.json()["memory_id"],
    ]
    hits = client.get("/memory/search", params={"q": "PELICAN metric units"}).json()
    assert hits["items"][0]["source_id"] == memory["memory_id"]
    assert hits["items"][0]["text"] == "The PELICAN pilot uses metric units."
    assert client.delete(f"/memory/{memory['memory_id']}").status_code == 204
    assert client.delete(f"/memory/{memory['memory_id']}").status_code == 404
    assert client.get("/memory").json()["total"] == 1


@pytest.mark.parametrize(
    "body",
    [
        {"content": ""},
        {"content": "   "},
        {"content": "x" * 2001},
        {"content": "note", "memory_type": "agent"},
        {"content": "note", "scope": "project"},
        {"content": "note", "source_type": "mission"},
    ],
)
def test_memory_request_shape_is_exact(client: TestClient, body: object) -> None:
    response = client.post("/memory", json=body)
    assert response.status_code == 422
    assert client.get("/memory").json()["total"] == 0


def test_secret_memory_is_refused(backend: Backend, client: TestClient) -> None:
    response = client.post("/memory", json={"content": "my api key = sk-" + "b" * 30})
    assert response.status_code == 422 and error_code(response) == "secret_rejected"
    assert client.get("/memory").json()["total"] == 0
    assert backend.embeddings is not None and backend.embeddings.calls == 0
    assert all("sk-" not in body for body in backend.bodies)


def test_search_bounds(client: TestClient) -> None:
    assert client.get("/memory/search").status_code == 422
    assert client.get("/memory/search", params={"q": " "}).status_code == 422
    assert (
        client.get("/memory/search", params={"q": "x", "limit": 21}).status_code == 422
    )
    assert client.get("/memory/search", params={"q": "nothing"}).json() == {"items": []}


# ---- cited missions


def test_cited_mission_answer_and_sources_over_the_api(
    backend: Backend, client: TestClient
) -> None:
    document = upload(client, "budget.txt", BUDGET.encode()).json()
    accepted = client.post(
        "/chat", json={"text": "What is the PELICAN budget?", "path": "complex"}
    )
    assert accepted.status_code == 202
    body = wait_for(client, accepted.json()["mission_id"])
    assert body["status"] == "completed", body
    (source,) = body["sources"]
    assert source["marker"] in body["answer_text"]
    assert source["title"] == "budget.txt"
    assert source["document_id"] == document["document_id"]
    assert source["kind"] == "document_chunk" and source["chunk_index"] == 0
    assert set(source) == {
        "marker",
        "kind",
        "source_id",
        "document_id",
        "title",
        "page",
        "section",
        "chunk_index",
        "memory_type",
        "label",
    }
    # The source text reaches no response but the answer's own claim.
    assert all(DOC_SENTINEL not in text for text in backend.bodies)


def test_missions_without_knowledge_list_no_sources(client: TestClient) -> None:
    accepted = client.post("/chat", json={"text": "Say hello."})
    body = wait_for(client, accepted.json()["mission_id"])
    assert body["status"] == "completed" and body["sources"] == []


def test_knowledge_runs_on_the_backend_event_loop(backend: Backend) -> None:
    # The service binds to the lifespan's loop; a second launch gets a new one.
    for _ in range(2):
        with backend.client() as client:
            assert upload(client, "a.txt", b"PELICAN alpha").status_code == 201
    with backend.client() as client:
        assert client.get("/files").json()["total"] == 2
