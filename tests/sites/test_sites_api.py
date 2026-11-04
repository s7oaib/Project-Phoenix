"""Sites HTTP round trip with a fake model (routes only, no backend services)."""

import json
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from phoenix.api.routes import command as command_routes
from phoenix.api.routes import sites as site_routes
from phoenix.api.routes.common import install_error_handlers
from phoenix.sites.service import SitesService

SPEC = {
    "type": "landing",
    "name": "Fake Site",
    "theme": {
        "primary": "#e8590c",
        "bg": "#0b0b0f",
        "text": "#f4f4f5",
        "font": "display",
    },
    "sections": [
        {
            "kind": k,
            "layout": "a",
            "title": k.title(),
            "body": "x",
            "items": [{"title": "i", "body": "j"}],
        }
        for k in ("hero", "features", "pricing", "footer")
    ],
}


class Model:
    def __init__(self) -> None:
        self.reply: Any = SPEC

    async def __call__(self, system: str, user: str) -> str:
        return json.dumps(self.reply)


@pytest.fixture
def api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, Model]]:
    model = Model()
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(site_routes.router)
    app.include_router(command_routes.router)
    app.state.sites = SitesService(lambda: tmp_path, lambda: model, lambda: [tmp_path])

    async def fake_chat(body: Any, request: Any) -> Any:
        return SimpleNamespace(mission_id="m-1")

    monkeypatch.setattr(command_routes, "chat", fake_chat)
    with TestClient(app) as client:  # keeps one event loop alive for the jobs
        yield client, model


def wait(client: TestClient, site: str) -> dict[str, Any]:
    for _ in range(300):
        body = client.get(f"/sites/{site}").json()
        if body["status"] != "building":
            return body
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_round_trip(api: tuple[TestClient, Model], tmp_path: Path) -> None:
    client, model = api
    assert client.get("/sites").json() == []
    created = client.post("/sites", json={"prompt": "a landing page for fake things"})
    assert created.status_code == 202
    body = created.json()
    assert body["status"] == "building" and body["phase"] == "planning"
    site = body["id"]
    done = wait(client, site)
    assert done["status"] == "ready" and done["phase"] == "ready"
    assert (
        done["name"] == "Fake Site"
        and done["head"] == 1
        and len(done["spec"]["sections"]) == 4
    )
    listed = client.get("/sites").json()
    assert [s["id"] for s in listed] == [site] and listed[0]["max"] == 1

    # edit via the model path
    model.reply = [
        {"op": "set_text", "section_id": "s1", "field": "title", "value": "Edited"}
    ]
    assert (
        client.post(
            f"/sites/{site}/edit", json={"instruction": "retitle", "section_id": "s1"}
        ).status_code
        == 202
    )
    done = wait(client, site)
    assert done["head"] == 2 and done["spec"]["sections"][0]["title"] == "Edited"

    # edit via the rule fallback (model returns junk)
    model.reply = "junk"
    client.post(f"/sites/{site}/edit", json={"instruction": "make the font serif"})
    done = wait(client, site)
    assert done["head"] == 3 and done["spec"]["theme"]["font"] == "serif"

    # rejected edit: spec unchanged, message returned
    model.reply = [{"op": "remove_section", "section_id": "s1"}]
    client.post(f"/sites/{site}/edit", json={"instruction": "do something odd"})
    done = wait(client, site)
    assert done["status"] == "failed" and "hero" in done["error"] and done["head"] == 3

    undone = client.post(f"/sites/{site}/undo").json()
    assert undone["head"] == 2 and undone["status"] == "ready"
    assert client.post(f"/sites/{site}/redo").json()["head"] == 3
    assert client.post(f"/sites/{site}/redo").json()["message"] == "Nothing to redo."

    page = client.get(f"/sites/{site}/preview")
    assert page.headers["content-type"].startswith("text/html")
    assert "data-section-id" in page.text and "phoenix-select" in page.text
    assert (tmp_path / "outputs" / "sites" / site / "site" / "index.html").is_file()


def test_errors_and_validation(api: tuple[TestClient, Model]) -> None:
    client, _ = api
    assert client.get("/sites/nope").status_code == 404
    assert client.get("/sites/nope/preview").status_code == 404
    assert client.post("/sites/nope/undo").status_code == 404
    assert client.post("/sites/nope/edit", json={"instruction": "x"}).status_code == 404
    assert client.get("/sites/..%2fetc").status_code in (404, 422)
    assert client.get("/sites/UPPER").status_code == 422
    assert client.post("/sites", json={"prompt": "   "}).status_code == 422
    assert client.post("/sites", json={"prompt": "x", "extra": 1}).status_code == 422
    site = client.post("/sites", json={"prompt": "site for tests"}).json()["id"]
    wait(client, site)
    assert (
        client.post(
            f"/sites/{site}/edit", json={"instruction": "x", "section_id": "s99"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/sites/{site}/edit", json={"instruction": "x", "section_id": "../x"}
        ).status_code
        == 422
    )


def test_command_endpoint(api: tuple[TestClient, Model]) -> None:
    client, _ = api
    made = client.post(
        "/command", json={"text": "design a website for a bakery"}
    ).json()
    assert made["kind"] == "site" and made["site_id"]
    wait(client, made["site_id"])
    edit = client.post(
        "/command",
        json={"text": "make the font mono", "active_site_id": made["site_id"]},
    ).json()
    assert edit["kind"] == "site" and edit["site_id"] == made["site_id"]
    wait(client, made["site_id"])
    assert (
        client.get(f"/sites/{made['site_id']}").json()["spec"]["theme"]["font"]
        == "mono"
    )
    other = client.post("/command", json={"text": "what is the weather"}).json()
    assert other == {
        "kind": "mission",
        "site_id": None,
        "mission_id": "m-1",
        "message": "",
    }
    assert client.post("/command", json={"text": ""}).status_code == 422
