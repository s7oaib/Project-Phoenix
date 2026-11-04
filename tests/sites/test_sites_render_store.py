"""Sites: spec validation, rendering of every type x layout x kind, the store."""

from html.parser import HTMLParser
from pathlib import Path

import pytest

from phoenix.sites.generator import fallback_spec
from phoenix.sites.render import render
from phoenix.sites.spec import (
    FONTS,
    KINDS,
    SITE_TYPES,
    SiteError,
    from_model,
    validate_spec,
)
from phoenix.sites.store import SiteStore, check_image_path, slugify

VOID = {"meta", "img", "br", "input", "link", "hr"}


class Check(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.ids: list[str] = []
        self.tags: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        self.tags.append(tag)
        for key, value in attrs:
            if key == "data-section-id":
                self.ids.append(value)
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        assert self.stack and self.stack.pop() == tag, f"unbalanced </{tag}>"


def parse(html: str) -> Check:
    check = Check()
    check.feed(html)
    check.close()
    assert not check.stack
    return check


def make_spec(
    site_type: str = "landing",
    layout: str = "a",
    font: str = "sans",
    slug: str = "demo",
) -> dict:
    items = [{"title": f"Item {n}", "body": "Body text."} for n in range(3)]
    middle = [k for k in KINDS if k not in ("hero", "footer")]
    kinds = ["hero", *middle, "footer"]
    return {
        "id": slug,
        "name": "Demo & Co",
        "type": site_type,
        "theme": {
            "primary": "#e8590c",
            "bg": "#0b0b0f",
            "text": "#f4f4f5",
            "font": font,
        },
        "sections": [
            {
                "id": f"s{n}",
                "kind": k,
                "layout": layout,
                "title": f"Title {k}",
                "body": "Some body.",
                "items": items,
                "image": None,
            }
            for n, k in enumerate(kinds, 1)
        ],
    }


@pytest.mark.parametrize("site_type", SITE_TYPES)
@pytest.mark.parametrize("layout", "abc")
def test_render_every_type_layout_kind(
    site_type: str, layout: str, tmp_path: Path
) -> None:
    for font in FONTS:
        spec = validate_spec(make_spec(site_type, layout, font))
        out = tmp_path / f"{site_type}-{layout}-{font}.html"
        out.write_text(render(spec), encoding="utf-8")
        check = parse(out.read_text(encoding="utf-8"))
        assert check.ids == [s.id for s in spec.sections]
        assert "script" not in check.tags  # no JS in the exported file


def test_empty_items_render_for_every_kind() -> None:
    data = make_spec()
    for section in data["sections"]:
        section["items"] = []
    for layout in "abc":
        for section in data["sections"]:
            section["layout"] = layout
        parse(render(validate_spec(data)))


def test_preview_has_click_select_script() -> None:
    html = render(validate_spec(make_spec()), select=True)
    assert "phoenix-select" in html and "data-section-id" in html
    assert "phoenix-select" not in render(validate_spec(make_spec()))


def test_all_text_is_escaped() -> None:
    data = make_spec()
    evil = '<script>alert(1)</script>"><img src=x onerror=alert(2)>&'
    data["name"] = evil
    for section in data["sections"]:
        section["title"] = section["body"] = evil
        section["items"] = [{"title": evil, "body": evil}]
    html = render(validate_spec(data), select=False)
    assert "<script" not in html and "<img src=x" not in html
    assert "&lt;script&gt;" in html
    assert "onerror" not in parse(html).tags


def test_spec_rules() -> None:
    good = make_spec()
    validate_spec(good)
    bad = make_spec()
    bad["sections"] = bad["sections"][1:]  # no hero first
    with pytest.raises(SiteError):
        validate_spec(bad)
    few = make_spec()
    few["sections"] = [few["sections"][0], few["sections"][-1]]
    with pytest.raises(SiteError):
        validate_spec(few)
    many = make_spec()
    many["sections"] += [dict(many["sections"][1], id=f"s{n}") for n in range(20, 23)]
    with pytest.raises(SiteError):
        validate_spec(many)
    color = make_spec()
    color["theme"]["primary"] = "red"
    with pytest.raises(SiteError):
        validate_spec(color)
    image = make_spec()
    image["sections"][0]["image"] = "../../secret.png"
    with pytest.raises(SiteError):
        validate_spec(image)


def test_from_model_repairs_loose_output() -> None:
    raw = {
        "type": "weird",
        "name": " Acme  ",
        "theme": {
            "primary": "nope",
            "bg": "#ffffff",
            "text": "#fefefe",
            "font": "comic",
        },
        "sections": [
            {"kind": "footer", "title": "f"},
            {"kind": "bogus"},
            {"kind": "pricing", "title": "P"},
            {"kind": "hero", "title": "H", "items": "no"},
            {"kind": "hero", "title": "second"},
        ],
    }
    spec = from_model(raw, "acme")
    assert [s.kind for s in spec.sections] == ["hero", "pricing", "footer"]
    assert [s.id for s in spec.sections] == ["s1", "s2", "s3"]
    assert spec.type == "landing" and spec.theme.font == "sans"
    assert spec.theme.text != "#fefefe"  # unreadable on white: repaired
    with pytest.raises(SiteError):
        from_model({"sections": [{"kind": "hero"}]}, "x")
    with pytest.raises(SiteError):
        from_model("nope", "x")


@pytest.mark.parametrize(
    "prompt,kind",
    [
        ("a site for my photography portfolio", "portfolio"),
        ("website for a bakery", "business"),
        ("launch page for my app", "landing"),
    ],
)
def test_fallback_spec_types(prompt: str, kind: str, tmp_path: Path) -> None:
    spec = fallback_spec(prompt, "x")
    assert spec.type == kind and 5 <= len(spec.sections) <= 7
    parse(render(spec))


def test_store_versions_undo_redo_and_tail_drop(tmp_path: Path) -> None:
    store = SiteStore(tmp_path / "sites")
    spec = validate_spec(make_spec())
    store.create(spec)
    assert store.meta("demo")["head"] == 1 and store.meta("demo")["max"] == 1
    assert (tmp_path / "sites/demo/site/index.html").is_file()
    for n in (2, 3):
        store.add_version("demo", validate_spec({**make_spec(), "name": f"v{n}"}))
    assert store.load("demo").name == "v3" and store.meta("demo")["max"] == 3
    assert store.move("demo", -1) is None and store.load("demo").name == "v2"
    assert "v2" in (tmp_path / "sites/demo/site/index.html").read_text(encoding="utf-8")
    assert store.move("demo", 1) is None and store.load("demo").name == "v3"
    assert store.move("demo", 1) == "Nothing to redo."
    store.move("demo", -1)
    store.move("demo", -1)
    assert store.move("demo", -1) == "Nothing to undo."
    assert store.meta("demo")["head"] == 1
    store.add_version(
        "demo", validate_spec({**make_spec(), "name": "branch"})
    )  # drops tail
    assert store.meta("demo") == {**store.meta("demo"), "head": 2, "max": 2}
    assert not (tmp_path / "sites/demo/versions/v0003.json").exists()
    assert store.move("demo", 1) == "Nothing to redo."
    assert store.list_sites()[0]["id"] == "demo"


def test_slugs_and_traversal(tmp_path: Path) -> None:
    store = SiteStore(tmp_path / "sites")
    for bad in ("../x", "..", "a/b", "a\\b", "", "UPPER", "-lead", "x" * 41, "a/../b"):
        with pytest.raises(SiteError):
            store.dir(bad)
    assert not store.exists("../x")
    assert slugify("../../Evil Name!!") == "evil-name"
    assert slugify("???") == "site"
    store.create(validate_spec(make_spec()))
    assert store.unique_slug("demo") == "demo-2"


def test_image_path_safety(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    (root / ".ssh").mkdir(parents=True)
    png = root / "ok.png"
    png.write_bytes(b"\x89PNG")
    (root / ".ssh" / "id.png").write_bytes(b"x")
    (root / ".env.png").write_bytes(b"x")
    (root / "k.pem").write_bytes(b"x")
    (root / "notes.txt").write_text("x")
    outside = tmp_path / "other.png"
    outside.write_bytes(b"x")
    assert check_image_path(str(png), [root]) == png.resolve()
    for bad in (
        outside,
        root / ".ssh" / "id.png",
        root / ".env.png",
        root / "k.pem",
        root / "notes.txt",
        root / "missing.png",
        root / ".." / "other.png",
        root,
    ):
        with pytest.raises(SiteError):
            check_image_path(str(bad), [root])
    with pytest.raises(SiteError):
        check_image_path("bad\x00path", [root])
    big = root / "big.png"
    big.write_bytes(b"0" * (5 * 1024 * 1024 + 1))
    with pytest.raises(SiteError):
        check_image_path(str(big), [root])


def test_copy_image_embeds_in_render(tmp_path: Path) -> None:
    store = SiteStore(tmp_path / "sites")
    store.create(validate_spec(make_spec()))
    src = tmp_path / "pic.png"
    src.write_bytes(b"\x89PNG-bytes")
    name = store.copy_image("demo", str(src), [tmp_path])
    assert (
        name == "assets/pic.png"
        and (tmp_path / "sites/demo/site/assets/pic.png").is_file()
    )
    data = make_spec()
    for section in data["sections"]:
        section["image"] = name
    store.add_version("demo", validate_spec(data))
    html = store.html("demo")
    assert "data:image/png;base64," in html and str(src) not in html
    parse(html)
