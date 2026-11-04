"""The build is divided across the roster: AIZEN plans, ROBIN polishes, LEVI checks, FRANKY builds."""

import asyncio
from pathlib import Path

from phoenix.sites.generator import fallback_spec
from phoenix.sites.review import contrast, polish_copy, verify
from phoenix.sites.service import SitesService


def test_robin_trims_copy_and_markdown() -> None:
    spec = fallback_spec("a bakery site", "x")
    data = spec.model_dump(mode="json")
    data["sections"][0]["title"] = "**" + " ".join(["word"] * 20) + "**"
    new, note = polish_copy(type(spec).model_validate(data))
    assert len(new.sections[0].title.split()) <= 8
    assert "*" not in new.sections[0].title
    assert note.endswith("lines tidied")


def test_levi_repairs_unreadable_text() -> None:
    spec = fallback_spec("a bakery site", "x")
    data = spec.model_dump(mode="json")
    data["theme"].update(bg="#101820", text="#111111", primary="#101820")
    new, note = verify(type(spec).model_validate(data))
    assert contrast(new.theme.text, new.theme.bg) >= 4.5
    assert note.startswith("fixed")


def test_levi_leaves_a_readable_theme_alone() -> None:
    spec = fallback_spec("a bakery site", "x")
    new, note = verify(spec)
    assert new == spec and note.startswith("all clear")


def test_build_and_edit_report_the_agents_in_order(tmp_path: Path) -> None:
    async def run() -> tuple[list[str], list[str], list[str]]:
        svc = SitesService(lambda: tmp_path, lambda: None)  # no model: keyword fallback
        view = svc.start_build("design a bakery website")
        await asyncio.gather(*list(svc._tasks))
        built = svc.get(view["id"])
        svc.start_edit(view["id"], "make the font serif")
        await asyncio.gather(*list(svc._tasks))
        edited = svc.get(view["id"])
        return (
            [a["name"] for a in built["agents"]],
            [a["state"] for a in built["agents"]],
            [a["name"] for a in edited["agents"]],
        )

    names, states, edit_names = asyncio.run(run())
    assert names == ["AIZEN", "ROBIN", "LEVI", "FRANKY"]
    assert set(states) == {"done"}
    assert edit_names == ["AIZEN", "LEVI", "FRANKY"]
