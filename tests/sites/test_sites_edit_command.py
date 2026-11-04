"""Sites: edit ops, rule fallback, generator with a fake model, intent routing."""

import asyncio
import json
from pathlib import Path

import pytest

from phoenix.sites.command import handle_command
from phoenix.sites.editor import apply_ops, rule_edit
from phoenix.sites.generator import build_spec, edit_spec
from phoenix.sites.service import SitesService
from phoenix.sites.spec import SiteError, SiteSpec, validate_spec

KINDS = ["hero", "features", "pricing", "testimonials", "contact", "footer"]


def spec6() -> SiteSpec:
    return validate_spec(
        {
            "id": "demo",
            "name": "Demo",
            "type": "business",
            "theme": {
                "primary": "#2563eb",
                "bg": "#ffffff",
                "text": "#0f172a",
                "font": "sans",
            },
            "sections": [
                {
                    "id": f"s{n}",
                    "kind": k,
                    "layout": "a",
                    "title": k.title(),
                    "body": "b",
                    "items": [],
                    "image": None,
                }
                for n, k in enumerate(KINDS, 1)
            ],
        }
    )


def kinds(spec: SiteSpec) -> list[str]:
    return [s.kind for s in spec.sections]


def test_each_op_applies() -> None:
    spec = spec6()
    out = apply_ops(
        spec,
        [
            {
                "op": "set_text",
                "section_id": "s1",
                "field": "title",
                "value": "Hello <b>",
            },
            {"op": "set_theme", "key": "primary", "value": "#ff0000"},
            {"op": "set_theme", "key": "font", "value": "Serif"},
            {"op": "set_layout", "section_id": "s2", "layout": "c"},
            {"op": "move_section", "section_id": "s3", "after_id": "s4"},
            {
                "op": "add_section",
                "after_id": "s5",
                "kind": "gallery",
                "title": "G",
                "body": "",
            },
            {"op": "remove_section", "section_id": "s2"},
        ],
    )
    assert out.sections[0].title == "Hello <b>" and out.theme.primary == "#ff0000"
    assert out.theme.font == "serif"
    assert kinds(out) == [
        "hero",
        "testimonials",
        "pricing",
        "contact",
        "gallery",
        "footer",
    ]
    assert out.sections[-2].id == "s7" and len(out.sections[-2].items) == 6
    assert spec == spec6()  # the input is never mutated


@pytest.mark.parametrize(
    "ops",
    [
        [{"op": "nope"}],
        [{"op": "set_text", "section_id": "s1", "field": "kind", "value": "x"}],
        [{"op": "set_text", "section_id": "s99", "field": "title", "value": "x"}],
        [
            {
                "op": "set_text",
                "section_id": "s1",
                "field": "title",
                "value": "x",
                "extra": 1,
            }
        ],
        [{"op": "set_theme", "key": "primary", "value": "blue"}],
        [{"op": "set_theme", "key": "font", "value": "comic"}],
        [{"op": "set_theme", "key": "bg", "value": "#0f172a"}],  # unreadable text
        [{"op": "remove_section", "section_id": "s1"}],
        [{"op": "remove_section", "section_id": "s6"}],
        [
            {
                "op": "add_section",
                "after_id": "s6",
                "kind": "gallery",
                "title": "x",
                "body": "",
            }
        ],
        [
            {
                "op": "add_section",
                "after_id": "s1",
                "kind": "hero",
                "title": "x",
                "body": "",
            }
        ],
        [{"op": "move_section", "section_id": "s1", "after_id": "s2"}],
        [{"op": "move_section", "section_id": "s2", "after_id": "s2"}],
        [{"op": "set_layout", "section_id": "s1", "layout": "d"}],
        [
            {"op": "set_image", "section_id": "s1", "path": "C:/x.png"}
        ],  # no copier given
        "not a list",
        [{"op": "remove_section", "section_id": "s2"}] * 21,
        [
            {"op": "set_text", "section_id": "s1", "field": "title", "value": "ok"},
            {"op": "set_layout", "section_id": "s2", "layout": "z"},
        ],  # valid first op: all rejected
    ],
)
def test_invalid_ops_reject_the_whole_edit(ops: object) -> None:
    spec = spec6()
    with pytest.raises(SiteError):
        apply_ops(spec, ops)
    assert spec == spec6()


def test_remove_below_minimum_rejected() -> None:
    spec = apply_ops(
        spec6(), [{"op": "remove_section", "section_id": f"s{n}"} for n in (2, 3, 4)]
    )
    assert len(spec.sections) == 3
    with pytest.raises(SiteError):
        apply_ops(spec, [{"op": "remove_section", "section_id": "s5"}])


def test_set_image_uses_the_copier_and_its_refusal() -> None:
    out = apply_ops(
        spec6(),
        [{"op": "set_image", "section_id": "s1", "path": "p.png"}],
        lambda path: "assets/p.png",
    )
    assert out.sections[0].image == "assets/p.png"

    def refuse(path: str) -> str:
        raise SiteError("image is outside the allowed folders")

    with pytest.raises(SiteError, match="outside"):
        apply_ops(
            spec6(), [{"op": "set_image", "section_id": "s1", "path": "x"}], refuse
        )


@pytest.mark.parametrize(
    "text,check",
    [
        (
            "make the title Welcome home",
            lambda s: s.sections[0].title == "Welcome home",
        ),
        (
            'Change the pricing title to "Plans"',
            lambda s: s.sections[2].title == "Plans",
        ),
        ("set the contact body to Call us", lambda s: s.sections[4].body == "Call us"),
        ("change the color to blue", lambda s: s.theme.primary == "#2563eb"),
        ("change the color to #FF8800", lambda s: s.theme.primary == "#ff8800"),
        ("make it red", lambda s: s.theme.primary == "#dc2626"),
        (
            "make the background black",
            lambda s: s.theme.bg == "#0a0a0a" and s.theme.text != "#0f172a",
        ),
        ("make it dark", lambda s: s.theme.bg == "#0b0b0f"),
        ("remove the pricing section", lambda s: "pricing" not in kinds(s)),
        ("delete testimonials", lambda s: "testimonials" not in kinds(s)),
        ("add a gallery section", lambda s: kinds(s)[-2] == "gallery"),
        (
            "add a testimonials section after the hero",
            lambda s: kinds(s)[1] == "testimonials",
        ),
        ("make the font serif", lambda s: s.theme.font == "serif"),
        ("use a monospace font", lambda s: s.theme.font == "mono"),
        (
            "move pricing above features",
            lambda s: kinds(s)[1:3] == ["pricing", "features"],
        ),
        (
            "move features below contact",
            lambda s: kinds(s)[-3:] == ["contact", "features", "footer"][-3:],
        ),
        ("set layout b for the pricing section", lambda s: s.sections[2].layout == "b"),
    ],
)
def test_rule_edit(text: str, check) -> None:
    spec = spec6()
    ops = rule_edit(spec, text)
    assert ops, text
    assert check(apply_ops(spec, ops)), text


def test_rule_edit_uses_selection_and_refuses_the_impossible() -> None:
    spec = spec6()
    ops = rule_edit(spec, "make the title Hi", selected="s3")
    assert ops and ops[0]["section_id"] == "s3"
    ops = rule_edit(spec, "remove this section", selected="s2")
    assert ops == [{"op": "remove_section", "section_id": "s2"}]
    assert rule_edit(spec, "what is the weather") is None
    assert rule_edit(spec, "move features above pricing") == []
    with pytest.raises(SiteError):
        rule_edit(spec, "move pricing above hero")
    with pytest.raises(SiteError):
        rule_edit(spec, "add a hero section")
    with pytest.raises(SiteError):
        rule_edit(spec, "remove the blog section")


class FakeModel:
    def __init__(self, *replies: object) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply)


GOOD_SPEC = {
    "type": "portfolio",
    "name": "Ada Lovelace",
    "theme": {
        "primary": "#112233",
        "bg": "#fafafa",
        "text": "#111111",
        "font": "serif",
    },
    "sections": [
        {"kind": k, "layout": "b", "title": k.title(), "body": "x"}
        for k in ("hero", "gallery", "contact", "footer")
    ],
}


def test_build_spec_model_retry_and_fallback() -> None:
    model = FakeModel("garbage", f"```json\n{json.dumps(GOOD_SPEC)}\n```")
    spec = asyncio.run(build_spec("a portfolio", "ada", model))
    assert spec.name == "Ada Lovelace" and len(model.calls) == 2
    assert "site designer" in model.calls[0][0]
    broken = FakeModel("garbage")
    spec = asyncio.run(build_spec("website for a bakery", "bakery", broken))
    assert (
        spec.type == "business" and len(broken.calls) == 2
    )  # one retry, then fallback
    spec = asyncio.run(
        build_spec("website for a bakery", "bakery", FakeModel(RuntimeError("down")))
    )
    assert spec.id == "bakery"
    assert asyncio.run(build_spec("a portfolio site", "p", None)).type == "portfolio"


def test_edit_spec_model_path_and_fallbacks() -> None:
    spec = spec6()
    op = [
        {"op": "set_text", "section_id": "s1", "field": "title", "value": "From model"}
    ]
    new, msg = asyncio.run(edit_spec(spec, "tweak it", None, FakeModel(op)))
    assert new and new.sections[0].title == "From model"
    # model returns junk -> rules take over
    new, _ = asyncio.run(
        edit_spec(spec, "make the font mono", None, FakeModel("not json"))
    )
    assert new and new.theme.font == "mono"
    # model down -> rules
    new, _ = asyncio.run(
        edit_spec(spec, "remove the pricing section", None, FakeModel(OSError()))
    )
    assert new and "pricing" not in kinds(new)
    # model proposes an invalid op, rules cannot help -> clear message, no change
    bad = [{"op": "remove_section", "section_id": "s1"}]
    new, msg = asyncio.run(edit_spec(spec, "do something odd", None, FakeModel(bad)))
    assert new is None and "hero" in msg
    # model says [] and no rule matches
    new, msg = asyncio.run(edit_spec(spec, "do something odd", None, FakeModel("[]")))
    assert new is None and msg
    # no model at all
    new, _ = asyncio.run(edit_spec(spec, "make it dark", None, None))
    assert new and new.theme.bg == "#0b0b0f"


def make_service(tmp_path: Path, model: FakeModel | None = None) -> SitesService:
    return SitesService(lambda: tmp_path, lambda: model)


async def settle(service: SitesService) -> None:
    while service.busy:
        await asyncio.sleep(0.01)


def test_command_routing(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = make_service(tmp_path)
        out = await handle_command(
            "Design a landing page for a coffee shop", None, service
        )
        assert out.kind == "site" and out.site_id
        site = out.site_id
        busy = await handle_command("make a website for dogs", None, service)
        assert busy.kind == "site" and "Still working" in busy.message
        await settle(service)
        assert service.get(site)["status"] == "ready"
        # without an active site, edit-like text is not ours
        for text in ("make the title Hi", "undo", "what time is it", ""):
            assert (await handle_command(text, None, service)).kind == "passthrough"
        # with one, edits / undo / redo are
        out = await handle_command("make the title Brew Time", site, service)
        assert out.kind == "site" and out.site_id == site
        await settle(service)
        assert service.get(site)["spec"]["sections"][0]["title"] == "Brew Time"
        out = await handle_command("undo", site, service)
        assert out.message == "Undone." and service.get(site)["head"] == 1
        assert (await handle_command("redo", site, service)).message == "Redone."
        assert (
            await handle_command("redo", site, service)
        ).message == "Nothing to redo."
        # open
        opened = await handle_command("open my coffee site", None, service)
        assert opened.kind == "site" and opened.site_id == site
        assert (
            await handle_command("open my zebra site", None, service)
        ).kind == "passthrough"
        # ordinary chat is untouched, even with a site active
        for text in (
            "what's the weather like",
            "make a coffee",
            "add 2 and 2",
            "summarise my files",
        ):
            assert (await handle_command(text, site, service)).kind == "passthrough", (
                text
            )
        # a new site while another is active
        out = await handle_command("build a new website for yoga", site, service)
        assert out.site_id and out.site_id != site
        await settle(service)

    asyncio.run(scenario())
