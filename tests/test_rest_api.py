"""PHX-104 REST API v1 (ADR-089).

Real components through Starlette's TestClient: the composition root, PHX-101
middleware, MissionService, PhoenixRuntime, FilesystemTool, PermissionEngine,
the settings store and alembic-migrated SQLite in a test directory under the
home folder (pytest's tmp_path lies inside AppData, which is not an acceptable
workspace). Only the model is
scripted, answering by the system prompt's role marker. No network, Ollama or
GPU is used; an autouse guard fails any httpx send or outbound socket connection.
"""

import asyncio
import json
import os
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from phoenix.api.missions import InvalidRequestError
from phoenix.api.routes import missions as mission_routes
from phoenix.api.routes import permissions as permission_routes
from phoenix.api.routes import setup as setup_routes
from phoenix.api.routes import system as system_routes
from phoenix.api.security import LocalApiSecurity
from phoenix.api.server import (
    ProviderSet,
    ServerConfig,
    build_server_app,
    get_services,
)
from phoenix.api.workspace import (
    WorkspaceErrorCode,
    app_data_directories,
    system_directories,
    validate_workspace_root,
)
from phoenix.core.config import AppSettings
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
from phoenix.tools.filesystem import FilesystemInputError

TOKEN = secrets.token_urlsafe(32)
PORT = 48766  # Host-header identity for in-process clients; nothing binds it
SECURITY = LocalApiSecurity(TOKEN, host="127.0.0.1", port=PORT)
FAST = ServerConfig(probe_timeout_seconds=0.5, startup_timeout_seconds=5)
MODELS = ("qwen3.5:2b", "qwen3.5:4b", "qwen3.5:9b", "qwen2.5-coder:7b")
PASS = '{"checks":[{"criterion":"Correct","passed":true,"evidence_summary":"Fine."}]}'
FILE_SENTINEL = "SENTINEL-CONTENT-93"
NOTES = f"Project codename: PELICAN-7. {FILE_SENTINEL}\n"
QUESTION = "What is the codename? USER-SENTINEL-11"
# Model output that must never reach a response (only the final answer may).
MODEL_SENTINELS = ("PLAN-SENTINEL", "STEP-SENTINEL", "SPECIALIST-SENTINEL")
UNKNOWN = "aaaaaaaa-0000-4000-8000-00000000000b"
MALFORMED = "not-a-uuid"
SETTLED = {"completed", "failed", "cancelled", "waiting_permission", "superseded"}


def role_of(request: GenerationRequest) -> str:
    return request.messages[0].text.splitlines()[0].removeprefix("PHOENIX ROLE: ")


class ScriptedProvider(ModelProvider):
    """Answers by role. While ``open`` is clear, every generation waits."""

    def __init__(self) -> None:
        super().__init__("ollama")
        self.replies: dict[str, str | Callable[[GenerationRequest], str]] = {
            # PHX-105: a chat without a path is triaged first.
            "triage": '{"path": "direct"}',
            "direct": lambda request: f"Answer: {request.messages[1].text}",
            "planner": json.dumps(
                {
                    "steps": [
                        {"objective": "Read notes.txt in the workspace PLAN-SENTINEL."},
                        {"objective": "State the project codename."},
                    ]
                }
            ),
            "assignment": json.dumps(
                {
                    "assignments": [
                        {"step_id": "s1", "domain": "documents"},
                        {"step_id": "s2", "domain": "intelligence"},
                    ]
                }
            ),
            "document_specialist": json.dumps(
                {
                    "tool_request": {
                        "operation": "read_text",
                        "relative_path": "notes.txt",
                    }
                }
            ),
            "document_answer": "The notes give the codename PELICAN-7 STEP-SENTINEL.",
            # PHX-120 (ADR-162): the backend wires PHX-113 capability steps, so
            # a documents step speaks the v5 document_capability roles.
            "document_capability": json.dumps(
                {
                    "tool_request": {
                        "operation": "read_text",
                        "relative_path": "notes.txt",
                    }
                }
            ),
            "document_capability_answer": (
                "The notes give the codename PELICAN-7 STEP-SENTINEL."
            ),
            "capability_verifier": PASS,
            "specialist": "The codename is PELICAN-7 SPECIALIST-SENTINEL.",
            "final_answer": "PELICAN-7",
            "verifier": PASS,
            "document_verifier": PASS,
            "final_verifier": PASS,
            "step_verifier": PASS,
        }
        self.open = threading.Event()
        self.open.set()
        self.waiting = 0
        self.roles: list[str] = []

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
        self.waiting += 1
        try:
            while not self.open.is_set():
                await asyncio.sleep(0.005)
        finally:
            self.waiting -= 1
        self.roles.append(role_of(request))
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
    """One data directory; each ``client()`` is one backend launch."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.settings = AppSettings(data_dir=tmp_path / "data", _env_file=None)  # type: ignore[call-arg]
        self.workspace = tmp_path / "workspace"
        self.workspace.mkdir()
        (self.workspace / "notes.txt").write_bytes(NOTES.encode("utf-8"))
        self.provider = ScriptedProvider()
        self.bodies: list[str] = []

    def app(self) -> FastAPI:
        provider = self.provider
        return build_server_app(
            self.settings,
            security=SECURITY,
            config=FAST,
            provider_factory=lambda config: ProviderSet(providers=(provider,)),
        )

    def client(self, *, token: str | None = TOKEN) -> TestClient:
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        client = TestClient(
            self.app(), base_url=f"http://127.0.0.1:{PORT}", headers=headers
        )

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


def remove_tree(root: Path) -> None:
    """Delete a test tree; junctions are unlinked first, never followed."""
    for path in sorted(root.rglob("*"), key=lambda item: -len(item.parts)):
        try:
            if os.lstat(path).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                os.rmdir(path)
        except (AttributeError, OSError):
            continue
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def base() -> Iterator[Path]:
    """A test directory outside AppData (pytest's tmp_path is inside it, and
    AppData is not an acceptable workspace), canonical and removed afterwards."""
    root = Path(os.path.realpath(tempfile.mkdtemp(prefix="phx104-", dir=Path.home())))
    try:
        yield root
    finally:
        remove_tree(root)


@pytest.fixture
def backend(base: Path) -> Backend:
    return Backend(base)


@pytest.fixture
def client(backend: Backend) -> Iterator[TestClient]:
    with backend.client() as client:
        yield client


def error_code(response: httpx.Response) -> str:
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "request_id"}
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]
    code = body["error"]["code"]
    assert isinstance(code, str)
    return code


def wait_for(
    client: TestClient, mission_id: str, *statuses: str, timeout: float = 20
) -> dict[str, Any]:
    wanted = set(statuses) or SETTLED
    deadline = time.monotonic() + timeout
    while True:
        response = client.get(f"/missions/{mission_id}")
        assert response.status_code == 200
        body: dict[str, Any] = response.json()
        if body["status"] in wanted:
            return body
        assert time.monotonic() < deadline, body["status"]
        time.sleep(0.01)


def until(predicate: Callable[[], bool], timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.005)


def chat(client: TestClient, text: str = "Say hello.", **extra: object) -> Any:
    response = client.post("/chat", json={"text": text, **extra})
    assert response.status_code == 202, response.text
    return response.json()


def set_workspace(client: TestClient, root: Path | str) -> Any:
    response = client.patch("/settings", json={"workspace_root": str(root)})
    assert response.status_code == 200, response.text
    return response.json()


def waiting_mission(client: TestClient, backend: Backend) -> tuple[str, str]:
    """A COMPLEX document mission waiting for permission: (mission, request)."""
    accepted = chat(client, QUESTION, path="complex")
    body = wait_for(client, accepted["mission_id"])
    assert body["status"] == "waiting_permission", body
    request = body["permission_request"]
    assert request["status"] == "pending"
    return accepted["mission_id"], request["request_id"]


# ---- route table, authentication and deferred routes


API_ROUTES = {
    ("POST", "/chat"),
    ("GET", "/missions"),
    ("GET", "/missions/{mission_id}"),
    ("POST", "/missions/{mission_id}/cancel"),
    ("POST", "/missions/{mission_id}/run-again"),
    ("GET", "/conversations"),
    ("GET", "/conversations/{conversation_id}/messages"),
    ("DELETE", "/conversations/{conversation_id}"),
    ("DELETE", "/conversations"),
    ("GET", "/permissions"),
    ("GET", "/permissions/{request_id}"),
    ("POST", "/permissions/{request_id}/approve"),
    ("POST", "/permissions/{request_id}/deny"),
    ("GET", "/grants"),
    ("POST", "/grants/{grant_id}/revoke"),
    ("GET", "/agents"),
    ("GET", "/models"),
    ("GET", "/settings"),
    ("PATCH", "/settings"),
    # PHX-108 (ADR-110, ADR-112).
    ("GET", "/onboarding"),
    ("GET", "/capabilities"),
}
# PHX-110 (ADR-132, ADR-133) adds the knowledge routes (tests/test_knowledge_api.py).
KNOWLEDGE_ROUTES = {
    ("POST", "/files/upload"),
    ("GET", "/files"),
    ("DELETE", "/files/{document_id}"),
    ("POST", "/memory"),
    ("GET", "/memory"),
    ("GET", "/memory/search"),
    ("DELETE", "/memory/{memory_id}"),
}


def routes_of(app: FastAPI) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    pending: list[object] = list(app.routes)
    while pending:
        route = pending.pop()
        original = getattr(route, "original_router", None)
        if original is not None:
            pending.extend(original.routes)
        elif isinstance(route, APIRoute):
            found |= {(method, route.path) for method in route.methods or ()}
    return found


def test_route_table_is_exact_and_deferred_routes_do_not_exist(
    backend: Backend, client: TestClient
) -> None:
    modules = (mission_routes, permission_routes, system_routes, setup_routes)
    declared = {
        (method, route.path)
        for module in modules
        for route in module.router.routes
        if isinstance(route, APIRoute)
        for method in route.methods or ()
    }
    assert declared == API_ROUTES
    assert routes_of(backend.app()) == API_ROUTES | KNOWLEDGE_ROUTES | {
        ("GET", "/health"),
        ("GET", "/ready"),
    }
    # PHX-110 added /files/upload and /memory/search; /tasks is not even a stub.
    response = client.request("GET", "/tasks")
    assert response.status_code == 404
    assert error_code(response) == "not_found"


def concrete(path: str, mission_id: str = UNKNOWN) -> str:
    path = path.replace("{mission_id}", mission_id)
    for name in ("conversation_id", "request_id", "grant_id"):
        path = path.replace("{" + name + "}", UNKNOWN)
    return path


def test_every_route_requires_the_token(backend: Backend) -> None:
    valid = {"Authorization": f"Bearer {TOKEN}"}
    with backend.client(token=None) as client:
        mission = client.post("/chat", json={"text": "hi"}, headers=valid).json()
        wait_for_with(client, valid, mission["mission_id"])
        for method, path in sorted(API_ROUTES):
            target = concrete(path, mission["mission_id"])
            for headers in (
                {},
                {"Authorization": f"Bearer {secrets.token_urlsafe(32)}"},
                {"Authorization": TOKEN},
            ):
                response = client.request(
                    method,
                    target,
                    headers=headers,
                    json={"text": "x", "duration": "session", "workspace_root": "C:\\"},
                )
                assert response.status_code == 401, (method, path)
                assert error_code(response) == "unauthorized"
                assert response.headers["WWW-Authenticate"] == "Bearer"
        # Nothing changed: the refused requests created, cancelled or deleted nothing.
        listed = client.get("/missions", headers=valid).json()
        assert [m["mission_id"] for m in listed["items"]] == [mission["mission_id"]]
        assert listed["items"][0]["status"] == "completed"
        settings = client.get("/settings", headers=valid).json()
        assert settings["workspace"]["status"] == "unset"


def wait_for_with(client: TestClient, headers: dict[str, str], mission_id: str) -> None:
    deadline = time.monotonic() + 20
    while (
        client.get(f"/missions/{mission_id}", headers=headers).json()["status"]
        not in SETTLED
    ):
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_routes_without_running_services_are_unavailable(backend: Backend) -> None:
    client = backend.client()  # no context manager: the lifespan never starts
    response = client.get("/missions")
    assert response.status_code == 503
    assert error_code(response) == "service_unavailable"


# ---- chat and missions


def test_chat_is_asynchronous_and_get_returns_the_answer(
    backend: Backend, client: TestClient
) -> None:
    backend.provider.open.clear()
    accepted = chat(client, "Say hello.")
    assert set(accepted) == {"mission_id", "conversation_id", "status", "rerun_of"}
    assert accepted["status"] == "queued" and accepted["rerun_of"] is None
    # The request returned while the model was still generating.
    until(lambda: backend.provider.waiting == 1)
    assert client.get(f"/missions/{accepted['mission_id']}").json()["status"] in {
        "queued",
        "running",
    }
    backend.provider.open.set()
    body = wait_for(client, accepted["mission_id"])
    assert body["status"] == "completed"
    assert body["request_text"] == "Say hello."
    assert body["answer_text"] == "Answer: Say hello."
    assert body["conversation_id"] == accepted["conversation_id"]
    assert body["path"] == "direct" and body["task_id"] is None
    assert body["permission_request"] is None and body["rerun_id"] is None
    # A second message joins the same conversation.
    second = chat(client, "Again.", conversation_id=accepted["conversation_id"])
    assert second["conversation_id"] == accepted["conversation_id"]
    wait_for(client, second["mission_id"])
    messages = client.get(
        f"/conversations/{accepted['conversation_id']}/messages"
    ).json()
    assert [m["role"] for m in messages["items"]] == [
        "user",
        "phoenix",
        "user",
        "phoenix",
    ]


def test_chat_path_is_optional_triage_decides_or_client_chooses(
    backend: Backend, client: TestClient
) -> None:
    # PHX-105 (ADR-090): omitted, PHOENIX triages; given, the client decides.
    roles = backend.provider.roles
    triaged = wait_for(client, chat(client, "Say hello.")["mission_id"])
    assert (triaged["status"], triaged["path"], triaged["triage_outcome"]) == (
        "completed",
        "direct",
        "decided",
    )
    assert roles == ["triage", "direct"]
    for path in ("direct", None):  # an explicit null is the same as omitted
        roles.clear()
        body = wait_for(client, chat(client, "Hi.", path=path)["mission_id"])
        assert body["status"] == "completed"
        if path:
            assert (body["path"], body["triage_outcome"]) == ("direct", "client")
            assert roles == ["direct"]  # the client's path bypasses triage
        else:
            assert (body["path"], body["triage_outcome"]) == ("direct", "decided")
            assert roles == ["triage", "direct"]
    listed = client.get("/missions").json()["items"]
    assert [item["triage_outcome"] for item in listed] == [
        "decided",
        "client",
        "decided",
    ]


def test_triaged_mission_rerun_after_approval_reuses_the_decision(
    backend: Backend, client: TestClient
) -> None:
    set_workspace(client, backend.workspace)
    backend.provider.replies["triage"] = '{"path": "complex"}'
    accepted = chat(client, QUESTION)
    waiting = wait_for(client, accepted["mission_id"])
    assert waiting["status"] == "waiting_permission", waiting
    assert (waiting["path"], waiting["triage_outcome"]) == ("complex", "decided")
    request_id = waiting["permission_request"]["request_id"]
    approved = client.post(
        f"/permissions/{request_id}/approve", json={"duration": "session"}
    ).json()
    rerun = wait_for(client, approved["mission_id"])
    assert rerun["status"] == "completed", rerun
    assert (rerun["path"], rerun["triage_outcome"]) == ("complex", "decided")
    # Triage ran once, for the original; the re-run reused its decision.
    assert backend.provider.roles.count("triage") == 1


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"text": ""},
        {"text": "   "},
        {"text": "x" * 16_001},
        {"text": 5},
        {"text": "hi", "path": "deep"},
        {"text": "hi", "conversation_id": MALFORMED},
        {"text": "hi", "mission_id": UNKNOWN},
        {"text": "hi", "agent_id": "phoenix"},
    ],
)
def test_chat_rejects_invalid_bodies(
    client: TestClient, body: dict[str, object]
) -> None:
    response = client.post("/chat", json=body)
    assert response.status_code == 422
    assert error_code(response) == "invalid_request"
    assert client.get("/missions").json()["total"] == 0


def test_chat_to_unknown_conversation_is_404(client: TestClient) -> None:
    response = client.post("/chat", json={"text": "hi", "conversation_id": UNKNOWN})
    assert response.status_code == 404
    assert error_code(response) == "not_found"


def test_mission_list_pagination_and_bounds(client: TestClient) -> None:
    first = chat(client, "one")
    for message in ("two", "three"):
        chat(client, message, conversation_id=first["conversation_id"])
    other = chat(client, "elsewhere")
    page = client.get("/missions", params={"limit": 2}).json()
    assert (page["limit"], page["offset"], page["total"]) == (2, 0, 4)
    assert len(page["items"]) == 2
    assert page["items"][0]["mission_id"] == first["mission_id"]
    rest = client.get("/missions", params={"limit": 2, "offset": 2}).json()
    assert [m["mission_id"] for m in rest["items"]][-1] == other["mission_id"]
    beyond = client.get("/missions", params={"offset": 10}).json()
    assert beyond["items"] == [] and beyond["total"] == 4
    scoped = client.get(
        "/missions", params={"conversation_id": first["conversation_id"]}
    ).json()
    assert scoped["total"] == 3
    # Summaries carry no text.
    assert "request_text" not in scoped["items"][0]
    assert "answer_text" not in scoped["items"][0]
    assert client.get("/missions").json()["limit"] == 50
    assert client.get("/missions", params={"limit": 200}).status_code == 200
    for params in (
        {"limit": 0},
        {"limit": 201},
        {"offset": -1},
        {"offset": 1_000_001},
        {"limit": "many"},
        {"conversation_id": MALFORMED},
    ):
        response = client.get("/missions", params=params)
        assert response.status_code == 422, params
        assert error_code(response) == "invalid_request"


def test_get_mission_404_and_422(client: TestClient) -> None:
    assert error_code(client.get(f"/missions/{UNKNOWN}")) == "not_found"
    assert client.get(f"/missions/{UNKNOWN}").status_code == 404
    for value in (MALFORMED, UNKNOWN.upper()):
        response = client.get(f"/missions/{value}")
        assert response.status_code == 422
        assert error_code(response) == "invalid_request"


def test_cancel_queued_and_running_missions(
    backend: Backend, client: TestClient
) -> None:
    backend.provider.open.clear()
    running = chat(client, "first")
    queued = chat(client, "second")
    until(lambda: backend.provider.waiting == 1)
    response = client.post(f"/missions/{queued['mission_id']}/cancel")
    assert response.status_code == 202
    assert response.json() == {
        "mission_id": queued["mission_id"],
        "outcome": "cancelled",
    }
    assert wait_for(client, queued["mission_id"])["status"] == "cancelled"
    response = client.post(f"/missions/{running['mission_id']}/cancel")
    assert response.status_code == 202
    assert response.json()["outcome"] == "cancelling"
    assert wait_for(client, running["mission_id"])["status"] == "cancelled"
    # Settled: a second cancel is a state conflict.
    response = client.post(f"/missions/{queued['mission_id']}/cancel")
    assert response.status_code == 409
    assert error_code(response) == "invalid_state"
    assert client.post(f"/missions/{UNKNOWN}/cancel").status_code == 404
    assert client.post(f"/missions/{MALFORMED}/cancel").status_code == 422


def test_run_again_after_restart_once(backend: Backend) -> None:
    backend.provider.open.clear()
    with backend.client() as client:
        interrupted = chat(client, "Interrupted.")
        until(lambda: backend.provider.waiting == 1)
    backend.provider.open.set()
    with backend.client() as client:
        body = wait_for(client, interrupted["mission_id"])
        assert (body["status"], body["failure_code"]) == ("failed", "interrupted")
        response = client.post(f"/missions/{interrupted['mission_id']}/run-again")
        assert response.status_code == 202
        rerun = response.json()
        assert rerun["rerun_of"] == interrupted["mission_id"]
        assert rerun["conversation_id"] == interrupted["conversation_id"]
        done = wait_for(client, rerun["mission_id"])
        assert done["status"] == "completed" and done["rerun_of"] == body["mission_id"]
        assert done["answer_text"] == "Answer: Interrupted."
        original = wait_for(client, interrupted["mission_id"])
        assert original["rerun_id"] == rerun["mission_id"]
        # A second run-again, or one of a completed mission, conflicts.
        for mission_id in (interrupted["mission_id"], rerun["mission_id"]):
            response = client.post(f"/missions/{mission_id}/run-again")
            assert response.status_code == 409
            assert error_code(response) == "invalid_state"
        assert client.post(f"/missions/{UNKNOWN}/run-again").status_code == 404
        assert client.post(f"/missions/{MALFORMED}/run-again").status_code == 422


# ---- permissions: the WAITING_PERMISSION flow


@pytest.mark.parametrize("choice", ["session", "always"])
def test_waiting_permission_approve_reruns_and_supersedes(
    backend: Backend, client: TestClient, choice: str
) -> None:
    settings = set_workspace(client, backend.workspace)
    assert settings["workspace"]["root"] == str(backend.workspace)
    assert settings["workspace"]["status"] == "active"
    mission_id, request_id = waiting_mission(client, backend)
    detail = client.get(f"/missions/{mission_id}").json()
    assert detail["task_id"] is not None
    assert "waiting_permission" in detail["task_states"]
    pending = client.get("/permissions").json()
    assert pending["total"] == 1
    request = pending["items"][0]
    assert request == client.get(f"/permissions/{request_id}").json()
    assert request["mission_id"] == mission_id
    assert request["tool_id"] == "filesystem.read_text"
    assert request["action"] == "read_text"
    assert request["resource"] == str(backend.workspace / "notes.txt")
    assert request["risk_level"] == "P1"
    response = client.post(
        f"/permissions/{request_id}/approve", json={"duration": choice}
    )
    assert response.status_code == 202, response.text
    approved = response.json()
    assert approved["superseded_mission_id"] == mission_id
    assert approved["duration"] == choice
    rerun = wait_for(client, approved["mission_id"])
    assert rerun["status"] == "completed", rerun
    assert rerun["answer_text"] == "PELICAN-7"
    assert rerun["rerun_of"] == mission_id
    original = client.get(f"/missions/{mission_id}").json()
    assert original["status"] == "superseded"
    assert original["rerun_id"] == approved["mission_id"]
    assert original["permission_request"]["status"] == "approved"
    assert client.get("/permissions").json()["total"] == 0
    grants = client.get("/grants").json()["items"]
    assert len(grants) == 1
    grant = grants[0]
    assert grant["duration"] == choice and grant["status"] == "active"
    assert grant["resource"] == request["resource"]
    assert (grant["tool_id"], grant["action"]) == ("filesystem.read_text", "read_text")
    # Approving again is a state conflict.
    response = client.post(
        f"/permissions/{request_id}/approve", json={"duration": choice}
    )
    assert response.status_code == 409
    assert error_code(response) == "invalid_state"
    # The planted file content and intermediate model output never left the API.
    for body in backend.bodies:
        assert FILE_SENTINEL not in body
        for sentinel in MODEL_SENTINELS:
            assert sentinel not in body


@pytest.mark.parametrize(
    "body",
    [
        {"duration": "session", "tool_id": "filesystem.write_text"},
        {"duration": "always", "resource": "C:\\Windows\\win.ini"},
        {"duration": "always", "actor_id": "phoenix"},
        {"duration": "session", "action": "delete_file"},
        {"duration": "once"},
        {"duration": "persistent"},
        {"duration": "task"},
        {},
        {"duration": ["session"]},
    ],
)
def test_approve_body_cannot_set_scope(
    backend: Backend, client: TestClient, body: dict[str, object]
) -> None:
    set_workspace(client, backend.workspace)
    mission_id, request_id = waiting_mission(client, backend)
    response = client.post(f"/permissions/{request_id}/approve", json=body)
    assert response.status_code == 422
    assert error_code(response) == "invalid_request"
    assert client.get(f"/permissions/{request_id}").json()["status"] == "pending"
    assert client.get(f"/missions/{mission_id}").json()["status"] == (
        "waiting_permission"
    )
    assert client.get("/grants", params={"include_inactive": True}).json()["total"] == 0


def test_permission_routes_404_and_422(client: TestClient) -> None:
    for method, path, body in (
        ("GET", f"/permissions/{UNKNOWN}", None),
        ("POST", f"/permissions/{UNKNOWN}/approve", {"duration": "session"}),
        ("POST", f"/permissions/{UNKNOWN}/deny", None),
        ("POST", f"/grants/{UNKNOWN}/revoke", None),
    ):
        response = client.request(method, path, json=body)
        assert response.status_code == 404, path
        assert error_code(response) == "not_found"
        response = client.request(method, path.replace(UNKNOWN, MALFORMED), json=body)
        assert response.status_code == 422, path
    for params in ({"limit": 0}, {"offset": -1}, {"include_inactive": "maybe"}):
        assert client.get("/grants", params=params).status_code == 422
    assert client.get("/permissions", params={"limit": 201}).status_code == 422


def test_deny_fails_the_mission_permission_denied(
    backend: Backend, client: TestClient
) -> None:
    set_workspace(client, backend.workspace)
    mission_id, request_id = waiting_mission(client, backend)
    refused = client.post(
        f"/permissions/{request_id}/deny", json={"tool_id": "filesystem.read_text"}
    )
    assert refused.status_code == 422
    response = client.post(f"/permissions/{request_id}/deny")
    assert response.status_code == 200
    assert response.json() == {
        "request_id": request_id,
        "mission_id": mission_id,
        "status": "failed",
        "failure_code": "permission_denied",
    }
    body = client.get(f"/missions/{mission_id}").json()
    assert (body["status"], body["failure_code"]) == ("failed", "permission_denied")
    assert body["permission_request"]["status"] == "denied"
    assert body["rerun_id"] is None
    response = client.post(f"/permissions/{request_id}/deny", json={})
    assert response.status_code == 409
    assert error_code(response) == "invalid_state"
    response = client.post(
        f"/permissions/{request_id}/approve", json={"duration": "always"}
    )
    assert response.status_code == 409
    assert client.get("/grants", params={"include_inactive": True}).json()["total"] == 0


def test_revoked_grant_is_no_longer_used(backend: Backend, client: TestClient) -> None:
    set_workspace(client, backend.workspace)
    _, request_id = waiting_mission(client, backend)
    approved = client.post(
        f"/permissions/{request_id}/approve", json={"duration": "always"}
    ).json()
    assert wait_for(client, approved["mission_id"])["status"] == "completed"
    # With the grant, the same request completes without asking.
    again = chat(client, QUESTION, path="complex")
    assert wait_for(client, again["mission_id"])["status"] == "completed"
    grant_id = client.get("/grants").json()["items"][0]["grant_id"]
    response = client.post(f"/grants/{grant_id}/revoke")
    assert response.status_code == 200
    assert response.json() == {"grant_id": grant_id, "status": "revoked"}
    assert client.get("/grants").json()["total"] == 0
    listed = client.get("/grants", params={"include_inactive": True}).json()
    assert [g["status"] for g in listed["items"]] == ["revoked"]
    response = client.post(f"/grants/{grant_id}/revoke")
    assert response.status_code == 409
    assert error_code(response) == "invalid_state"
    # Revoked: the next mission waits for permission again.
    waiting_mission(client, backend)


# ---- settings and the workspace


def test_settings_read_only_values_and_patch_scope(
    backend: Backend, client: TestClient
) -> None:
    body = client.get("/settings").json()
    assert body["workspace"] == {"root": None, "status": "unset", "updated_at": None}
    assert body["default_mission_path"] == "auto"  # PHX-108 (ADR-111)
    assert body["retention_days"] == 30
    assert body["api"] == {"host": "127.0.0.1", "port": PORT}
    assert body["model_tiers"]["assignment"][0] == {
        "provider_id": "ollama",
        "model_id": "qwen3.5:9b",
    }
    assert body["default_tier"][-1]["model_id"] == "qwen3.5:2b"
    patches: tuple[dict[str, object], ...] = (
        {},
        {"workspace_root": str(backend.workspace), "retention_days": 1},
        {"api": {"port": 1}},
        {"model_tiers": {}},
        {"workspace_root": None},
        {"workspace_root": 7},
    )
    for patch in patches:
        response = client.patch("/settings", json=patch)
        assert response.status_code == 422, patch
        assert error_code(response) == "invalid_request"
    assert client.get("/settings").json() == body


def invalid_roots(backend: Backend) -> list[tuple[str, WorkspaceErrorCode]]:
    tmp = backend.tmp
    file = backend.workspace / "notes.txt"
    roots = [
        (str(tmp / "missing"), WorkspaceErrorCode.NOT_FOUND),
        (str(file), WorkspaceErrorCode.NOT_DIRECTORY),
        ("relative\\folder", WorkspaceErrorCode.INVALID),
        ("relative/folder", WorkspaceErrorCode.INVALID),
        ("\\\\server\\share\\docs", WorkspaceErrorCode.INVALID),
        ("//server/share/docs", WorkspaceErrorCode.INVALID),
        ("\\\\?\\C:\\Users", WorkspaceErrorCode.INVALID),
        ("\\\\.\\PhysicalDrive0", WorkspaceErrorCode.INVALID),
        (str(backend.workspace / ".." / "workspace"), WorkspaceErrorCode.INVALID),
        (str(backend.workspace) + "\x00", WorkspaceErrorCode.INVALID),
        (tmp.anchor, WorkspaceErrorCode.DRIVE_ROOT),
        (str(backend.settings.data_dir), WorkspaceErrorCode.DATA_DIRECTORY),
        (str(tmp), WorkspaceErrorCode.DATA_DIRECTORY),  # contains the data dir
    ]
    if sys.platform == "win32":
        roots.append(("C:folder", WorkspaceErrorCode.INVALID))
        system_root = os.environ["SystemRoot"]
        roots.append((system_root, WorkspaceErrorCode.SYSTEM_DIRECTORY))
        roots.append(
            (os.path.join(system_root, "System32"), WorkspaceErrorCode.SYSTEM_DIRECTORY)
        )
        # pytest's own temp folder lies inside AppData\\Local (R18).
        # Use the canonical spelling: on runners the temp dir is an 8.3 short
        # name (e.g. RUNNER~1), which is refused earlier as a non-real path.
        roots.append(
            (os.path.realpath(tempfile.gettempdir()), WorkspaceErrorCode.APP_DATA)
        )
        roots.append((os.environ["APPDATA"], WorkspaceErrorCode.APP_DATA))
    else:
        roots.append(("/etc", WorkspaceErrorCode.SYSTEM_DIRECTORY))
    return roots


def test_workspace_validation_rejects_bad_roots(
    backend: Backend, client: TestClient
) -> None:
    for root, code in invalid_roots(backend):
        response = client.patch("/settings", json={"workspace_root": root})
        assert response.status_code == 422, root
        assert error_code(response) == code.value, root
        assert response.json()["error"]["message"] == "Request failed."
    assert client.get("/settings").json()["workspace"]["status"] == "unset"


def test_workspace_rejects_links_and_junctions(
    backend: Backend, client: TestClient
) -> None:
    target = backend.workspace
    link = backend.tmp / "linked"
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        pass  # symlinks need a privilege on Windows; the junction covers links
    else:
        response = client.patch("/settings", json={"workspace_root": str(link)})
        assert error_code(response) == "workspace_link"
        inner = link / "inner"
        (target / "inner").mkdir()
        response = client.patch("/settings", json={"workspace_root": str(inner)})
        assert error_code(response) == "workspace_link"  # a link in the chain
    if sys.platform == "win32":
        junction = backend.tmp / "junction"
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
            check=True,
            capture_output=True,
        )
        response = client.patch("/settings", json={"workspace_root": str(junction)})
        assert response.status_code == 422
        assert error_code(response) == "workspace_link"
        (target / "sub").mkdir()
        response = client.patch(
            "/settings", json={"workspace_root": str(junction / "sub")}
        )
        assert error_code(response) == "workspace_link"
    assert client.get("/settings").json()["workspace"]["status"] == "unset"


def test_validation_unit_rules(base: Path) -> None:
    tmp_path = base
    root = tmp_path / "docs"
    root.mkdir()
    data = tmp_path / "data"
    assert validate_workspace_root(str(root), data_dir=data) == str(root)
    # Separators are normalized; the stored form is the normalized path.
    mixed = str(root).replace("\\", "/")
    assert validate_workspace_root(mixed, data_dir=data) == os.path.normpath(mixed)
    windows = system_directories(
        {"SystemRoot": "D:\\Win", "ProgramData": "relative"}, "win32"
    )
    assert windows[0] == "D:\\Win" and "relative" not in windows
    # A sanitised environment cannot hide the system folders (D4).
    bare = system_directories({}, "win32")
    for folder in ("C:\\Windows", "C:\\Program Files", "C:\\ProgramData"):
        assert folder in bare
    if sys.platform == "win32":
        with pytest.raises(ValueError) as refused:
            validate_workspace_root(
                os.environ["SystemRoot"], data_dir=data, system=bare
            )
        assert str(refused.value) == "workspace_system_directory"
        assert any(
            folder.lower().endswith("appdata")
            for folder in app_data_directories({"USERPROFILE": "C:\\Users\\u"}, "win32")
        )
    for name in ("$Recycle.Bin", "System Volume Information"):
        special = tmp_path / name / "inner"
        special.mkdir(parents=True)
        with pytest.raises(ValueError) as refused:
            validate_workspace_root(str(special), data_dir=data)
        assert str(refused.value) == "workspace_system_directory"
    with pytest.raises(ValueError) as refused:
        validate_workspace_root(str(root), data_dir=data, app_data=(str(tmp_path),))
    assert str(refused.value) == "workspace_app_data"
    assert "/etc" in system_directories({}, "linux")
    with pytest.raises(ValueError) as raised:
        validate_workspace_root(str(root), data_dir=data, system=(str(tmp_path),))
    assert str(raised.value) == "workspace_system_directory"
    assert str(root) not in str(raised.value)
    for value in (None, 5, "", "x" * 4097):
        with pytest.raises(ValueError):
            validate_workspace_root(value, data_dir=data)


def test_workspace_persists_and_unavailable_root_is_reported(
    backend: Backend,
) -> None:
    with backend.client() as client:
        set_workspace(client, backend.workspace)
    with backend.client() as client:
        workspace = client.get("/settings").json()["workspace"]
        assert workspace["root"] == str(backend.workspace)
        assert workspace["status"] == "active"
        assert workspace["updated_at"] is not None
        waiting_mission(client, backend)  # document steps work after a restart
    moved = backend.tmp / "moved"
    backend.workspace.rename(moved)
    with backend.client() as client:
        workspace = client.get("/settings").json()["workspace"]
        assert workspace["status"] == "unavailable"
        set_workspace(client, moved)
        assert client.get("/settings").json()["workspace"]["status"] == "active"
    engine = create_engine(f"sqlite:///{backend.settings.data_dir / 'phoenix.db'}")
    try:
        assert "app_settings" in inspect(engine).get_table_names()
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT key, value FROM app_settings"))
            assert [tuple(row) for row in rows] == [("workspace_root", str(moved))]
    finally:
        engine.dispose()


def test_workspace_change_applies_to_later_missions_only(
    backend: Backend, client: TestClient
) -> None:
    first = backend.workspace
    second = backend.tmp / "second"
    second.mkdir()
    (second / "notes.txt").write_bytes(NOTES.encode("utf-8"))
    set_workspace(client, first)
    _, request_id = waiting_mission(client, backend)
    approved = client.post(
        f"/permissions/{request_id}/approve", json={"duration": "always"}
    ).json()
    assert wait_for(client, approved["mission_id"])["status"] == "completed"
    # A mission running under the first root keeps it across the change.
    backend.provider.open.clear()
    running = chat(client, QUESTION, path="complex")
    until(lambda: backend.provider.waiting == 1)
    assert set_workspace(client, second)["workspace"]["root"] == str(second)
    backend.provider.open.set()
    body = wait_for(client, running["mission_id"])
    assert body["status"] == "completed", body  # used the first root's grant
    # Later missions use the second root; the first root's grant does not apply.
    _, request_id = waiting_mission(client, backend)
    request = client.get(f"/permissions/{request_id}").json()
    assert request["resource"] == str(second / "notes.txt")
    grants = client.get("/grants").json()["items"]
    assert [g["resource"] for g in grants] == [str(first / "notes.txt")]


# ---- conversations, agents and models


def test_conversations_list_messages_delete_and_clear_all(
    backend: Backend, client: TestClient
) -> None:
    set_workspace(client, backend.workspace)
    a = chat(client, "alpha")
    b = chat(client, "beta")
    wait_for(client, a["mission_id"])
    wait_for(client, b["mission_id"])
    _, request_id = waiting_mission(client, backend)
    approved = client.post(
        f"/permissions/{request_id}/approve", json={"duration": "always"}
    ).json()
    wait_for(client, approved["mission_id"])
    listed = client.get("/conversations").json()
    assert listed["total"] == 3
    assert listed["items"][0]["conversation_id"] == a["conversation_id"]
    assert (
        client.get("/conversations", params={"limit": 1, "offset": 2}).json()["items"][
            0
        ]["conversation_id"]
        != b["conversation_id"]
    )
    messages = client.get(
        f"/conversations/{a['conversation_id']}/messages", params={"limit": 1}
    ).json()
    assert messages["total"] == 2 and len(messages["items"]) == 1
    assert messages["items"][0]["text"] == "alpha"
    for params in ({"limit": 0}, {"offset": -1}):
        assert client.get("/conversations", params=params).status_code == 422
    response = client.delete(f"/conversations/{a['conversation_id']}")
    assert response.status_code == 204 and response.content == b""
    for response in (
        client.get(f"/conversations/{a['conversation_id']}/messages"),
        client.delete(f"/conversations/{a['conversation_id']}"),
        client.get(f"/missions/{a['mission_id']}"),
    ):
        assert response.status_code == 404
        assert error_code(response) == "not_found"
    assert client.delete(f"/conversations/{MALFORMED}").status_code == 422
    assert client.get(f"/conversations/{MALFORMED}/messages").status_code == 422
    assert client.get("/conversations").json()["total"] == 2
    response = client.delete("/conversations")
    assert response.status_code == 204
    assert client.get("/conversations").json()["total"] == 0
    assert client.get("/missions").json()["total"] == 0
    # Grants outlive chat history until revoked.
    assert client.get("/grants").json()["total"] == 1


def test_agents_and_models(backend: Backend, client: TestClient) -> None:
    agents = client.get("/agents").json()
    ids = [agent["agent_id"] for agent in agents]
    assert "phoenix" in ids and "strategist" in ids and "verifier" in ids
    assert all(set(agent) == {"agent_id", "alias", "role"} for agent in agents)
    models = {m["model_id"]: m for m in client.get("/models").json()}
    assert set(models) == set(MODELS)
    for model in models.values():
        assert model["registered"] is True
        assert model["provider_status"] == "healthy"
        assert model["generating"] is False
        assert model["context_limit_tokens"] == 16384
    assert models["qwen2.5-coder:7b"]["roles"] == ["build_engineer"]
    assert "default" in models["qwen3.5:2b"]["roles"]


def test_cancel_and_shutdown_reach_a_mission_on_the_previous_runtime(
    backend: Backend,
) -> None:
    backend.provider.open.clear()
    with backend.client() as client:
        services = get_services(client.app)  # type: ignore[arg-type]
        assert services is not None
        initial = services.missions.runtime
        with pytest.raises(TypeError):
            services.missions.use_runtime(object())  # type: ignore[arg-type]
        cancelled = chat(client, "Cancel me.")
        until(lambda: backend.provider.waiting == 1)
        set_workspace(client, backend.workspace)
        assert services.missions.runtime is not initial
        response = client.post(f"/missions/{cancelled['mission_id']}/cancel")
        assert response.json()["outcome"] == "cancelling"
        assert wait_for(client, cancelled["mission_id"])["status"] == "cancelled"
        backend.provider.open.clear()
        interrupted = chat(client, "Interrupt me.")
        until(lambda: backend.provider.waiting == 1)
        set_workspace(client, backend.workspace)  # a third runtime
    backend.provider.open.set()
    with backend.client() as client:
        body = wait_for(client, interrupted["mission_id"])
        assert (body["status"], body["failure_code"]) == ("failed", "interrupted")


# ---- review fixes (D1-D5 and nits)


def junction(link: Path, target: Path) -> None:
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        check=True,
        capture_output=True,
    )


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows-only")
def test_root_swapped_for_a_junction_fails_the_next_mission(
    backend: Backend, client: TestClient
) -> None:
    """D1: the stored root is validated again before every mission."""
    set_workspace(client, backend.workspace)
    _, request_id = waiting_mission(client, backend)
    approved = client.post(
        f"/permissions/{request_id}/approve", json={"duration": "always"}
    ).json()
    assert wait_for(client, approved["mission_id"])["status"] == "completed"
    outside = backend.tmp / "outside"
    outside.mkdir()
    secret = "OUTSIDE-SECRET-77"
    (outside / "notes.txt").write_text(f"Project codename: {secret}\n")
    backend.workspace.rename(backend.tmp / "moved")
    junction(backend.workspace, outside)
    calls = len(backend.provider.roles)
    swapped = chat(client, QUESTION, path="complex")
    body = wait_for(client, swapped["mission_id"])
    assert (body["status"], body["failure_code"]) == ("failed", "tool_rejected")
    assert len(backend.provider.roles) == calls  # the runtime never ran it
    workspace = client.get("/settings").json()["workspace"]
    assert workspace["status"] == "unavailable"
    assert workspace["root"] == str(backend.workspace)
    # Later missions run without document steps instead of failing.
    later = chat(client, "Still there?")
    assert wait_for(client, later["mission_id"])["status"] == "completed"
    assert all(secret not in text for text in backend.bodies)


def test_approval_outside_the_active_root_is_refused(
    backend: Backend, client: TestClient
) -> None:
    """D2: no grant is stored for a request made under another workspace."""
    second = backend.tmp / "second"
    second.mkdir()
    set_workspace(client, backend.workspace)
    mission_id, request_id = waiting_mission(client, backend)
    set_workspace(client, second)
    response = client.post(
        f"/permissions/{request_id}/approve", json={"duration": "always"}
    )
    assert response.status_code == 409
    assert error_code(response) == "workspace_changed"
    assert client.get(f"/permissions/{request_id}").json()["status"] == "pending"
    assert client.get("/grants", params={"include_inactive": True}).json()["total"] == 0
    assert client.post(f"/permissions/{request_id}/deny").status_code == 200
    assert client.get(f"/missions/{mission_id}").json()["failure_code"] == (
        "permission_denied"
    )


def test_web_research_request_is_approved_without_a_workspace(
    backend: Backend,
) -> None:
    """PHX-111: a research request names the provider, not a workspace path.

    The re-run's DuckDuckGo request reaches the autouse network guard, so the
    mission fails explicitly as tool_failed: nothing leaves the test process.
    """
    backend.settings = AppSettings(
        data_dir=backend.tmp / "data",
        web_research_enabled=True,
        _env_file=None,  # type: ignore[call-arg]
    )
    backend.provider.replies |= {
        "research_planner": json.dumps(
            {"steps": [{"objective": "Search the web for the codename."}]}
        ),
        "assignment": json.dumps(
            {"assignments": [{"step_id": "s1", "domain": "web_research"}]}
        ),
        "research_specialist": json.dumps(
            {"search_request": {"query": "pelican codename"}}
        ),
    }
    with backend.client() as client:
        accepted = chat(client, QUESTION, path="complex")
        waiting = wait_for(client, accepted["mission_id"])
        assert waiting["status"] == "waiting_permission", waiting
        request = waiting["permission_request"]
        pending = client.get(f"/permissions/{request['request_id']}").json()
        assert (pending["tool_id"], pending["resource"], pending["risk_level"]) == (
            "web.research",
            "search:duckduckgo",
            "P1",
        )
        response = client.post(
            f"/permissions/{request['request_id']}/approve",
            json={"duration": "session"},
        )
        assert response.status_code == 202, response.text
        rerun = wait_for(client, response.json()["mission_id"])
        assert (rerun["status"], rerun["failure_code"]) == ("failed", "tool_failed")
        (grant,) = client.get("/grants").json()["items"]
        assert (grant["tool_id"], grant["resource"]) == (
            "web.research",
            "search:duckduckgo",
        )


def test_document_create_request_is_p2_and_session_only(
    backend: Backend, client: TestClient
) -> None:
    """PHX-120 (ADR-162): the API grant store admits PHX-113 grants; a P2
    document.create may be approved for this session only, never "always"."""
    set_workspace(client, backend.workspace)
    backend.provider.replies |= {
        "planner": json.dumps({"steps": [{"objective": "Save a summary."}]}),
        "assignment": json.dumps(
            {"assignments": [{"step_id": "s1", "domain": "documents"}]}
        ),
        "document_capability": json.dumps(
            {
                "tool_request": {
                    "operation": "create_document",
                    "relative_path": "summary.md",
                    "title": "Summary",
                    "sections": [{"heading": "Codename", "paragraphs": ["PELICAN"]}],
                }
            }
        ),
        "document_capability_answer": "Saved summary.md.",
    }
    accepted = chat(client, QUESTION, path="complex")
    waiting = wait_for(client, accepted["mission_id"])
    assert waiting["status"] == "waiting_permission", waiting
    request_id = waiting["permission_request"]["request_id"]
    pending = client.get(f"/permissions/{request_id}").json()
    assert (pending["tool_id"], pending["risk_level"]) == ("document.create", "P2")
    assert pending["resource"] == str(backend.workspace / "summary.md")
    assert not (backend.workspace / "summary.md").exists()
    refused = client.post(
        f"/permissions/{request_id}/approve", json={"duration": "always"}
    )
    assert refused.status_code == 422, refused.text
    assert client.get("/grants").json()["items"] == []
    response = client.post(
        f"/permissions/{request_id}/approve", json={"duration": "session"}
    )
    assert response.status_code == 202, response.text
    rerun = wait_for(client, response.json()["mission_id"])
    assert rerun["status"] == "completed", rerun
    assert "PELICAN" in (backend.workspace / "summary.md").read_text("utf-8")
    (grant,) = client.get("/grants").json()["items"]
    assert (grant["tool_id"], grant["duration"]) == ("document.create", "session")


def test_media_request_is_approved_without_a_workspace(
    backend: Backend, client: TestClient
) -> None:
    """PHX-120 (ADR-162): a YouTube request names no workspace path, so it is
    approvable without one. The re-run has no stored API key or reaches the
    autouse network guard, so it fails explicitly as tool_failed."""
    backend.provider.replies |= {
        "planner": json.dumps({"steps": [{"objective": "Find a lofi video."}]}),
        "assignment": json.dumps(
            {"assignments": [{"step_id": "s1", "domain": "media"}]}
        ),
        "media_specialist": json.dumps(
            {
                "tool_request": {
                    "service": "youtube",
                    "operation": "search",
                    "query": "lofi",
                }
            }
        ),
    }
    accepted = chat(client, QUESTION, path="complex")
    waiting = wait_for(client, accepted["mission_id"])
    assert waiting["status"] == "waiting_permission", waiting
    request_id = waiting["permission_request"]["request_id"]
    pending = client.get(f"/permissions/{request_id}").json()
    assert (pending["tool_id"], pending["risk_level"]) == ("youtube.search", "P1")
    response = client.post(
        f"/permissions/{request_id}/approve", json={"duration": "session"}
    )
    assert response.status_code == 202, response.text
    rerun = wait_for(client, response.json()["mission_id"])
    assert (rerun["status"], rerun["failure_code"]) == ("failed", "tool_failed")
    (grant,) = client.get("/grants").json()["items"]
    assert grant["tool_id"] == "youtube.search"


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows-only")
def test_data_directory_overlap_uses_the_real_path(base: Path) -> None:
    """D3: a junctioned data directory still protects its real target."""
    real = base / "realdata"
    (real / "sub").mkdir(parents=True)
    link = base / "datalink"
    junction(link, real)
    for root in (real, real / "sub", base):
        with pytest.raises(ValueError) as refused:
            validate_workspace_root(str(root), data_dir=link)
        assert str(refused.value) == "workspace_data_directory"


def test_stored_root_is_canonical(backend: Backend, client: TestClient) -> None:
    """D5: the root is stored in its real-path spelling."""
    given = str(backend.workspace).upper() if sys.platform == "win32" else None
    if given is None:
        pytest.skip("case-insensitive paths are Windows-only")
    canonical = os.path.realpath(backend.workspace)
    assert set_workspace(client, given)["workspace"]["root"] == canonical
    _, request_id = waiting_mission(client, backend)
    request = client.get(f"/permissions/{request_id}").json()
    assert request["resource"] == os.path.join(canonical, "notes.txt")


def test_failed_submit_leaves_no_empty_conversation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = get_services(client.app)  # type: ignore[arg-type]
    assert services is not None

    async def refuse(*args: object) -> str:
        raise InvalidRequestError("refused")

    monkeypatch.setattr(services.missions, "submit", refuse)
    response = client.post("/chat", json={"text": "hi"})
    assert response.status_code == 422
    assert client.get("/conversations").json()["total"] == 0


def test_request_bodies_are_capped(backend: Backend, client: TestClient) -> None:
    big = {"text": "x" * (64 * 1024)}
    response = client.post("/chat", json=big)
    assert response.status_code == 413
    assert error_code(response) == "payload_too_large"

    def chunks() -> Iterator[bytes]:
        yield b'{"text": "'
        for _ in range(80):
            yield b"y" * 1024
        yield b'"}'

    response = client.post(
        "/chat", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
    assert error_code(response) == "payload_too_large"
    padded = '{"text": "hi"' + " " * (60 * 1024) + "}"
    response = client.post(
        "/chat", content=padded, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 202  # under the cap: parsed normally
    for method, path in (("PATCH", "/settings"), ("POST", f"/grants/{UNKNOWN}/revoke")):
        assert client.request(method, path, json=big).status_code == 413
    # Authentication still comes first.
    response = client.post("/chat", json=big, headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401
    assert client.get("/conversations").json()["total"] == 1


def test_set_root_errors_are_explicit(
    backend: Backend, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = get_services(client.app)  # type: ignore[arg-type]
    assert services is not None

    def refuse(root: str | None) -> object:
        raise FilesystemInputError("refused")

    with monkeypatch.context() as patch:
        patch.setattr(services.workspace, "_build", refuse)
        response = client.patch(
            "/settings", json={"workspace_root": str(backend.workspace)}
        )
        assert response.status_code == 422
        assert error_code(response) == "workspace_invalid"
    assert client.get("/settings").json()["workspace"]["status"] == "unset"
    assert client.portal is not None
    client.portal.call(services.missions.stop)
    response = client.patch(
        "/settings", json={"workspace_root": str(backend.workspace)}
    )
    assert response.status_code == 503
    assert error_code(response) == "service_unavailable"
