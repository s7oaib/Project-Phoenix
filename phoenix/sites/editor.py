# ruff: noqa: E501
"""Edit operations: validate and apply, all-or-nothing, plus a rule-based planner.

The seven operations are the only ways a site changes. ``apply_ops`` works on a
copy and re-validates the result as a SiteSpec, so any invalid or unsafe op
rejects the whole edit and the stored spec is untouched. ``rule_edit`` turns
simple English into ops without a model (the fallback when the model fails).
"""

import copy
import re
from collections.abc import Callable
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from phoenix.sites.spec import (
    FONTS,
    HEX_RE,
    KINDS,
    MIDDLE_KINDS,
    SiteError,
    SiteSpec,
    contrast,
    readable,
    validate_spec,
)

MAX_OPS = 20


class _Op(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SetText(_Op):
    op: Literal["set_text"]
    section_id: str
    field: Literal["title", "body"]
    value: str


class SetTheme(_Op):
    op: Literal["set_theme"]
    key: Literal["primary", "bg", "text", "font"]
    value: str


class AddSection(_Op):
    op: Literal["add_section"]
    after_id: str
    kind: str
    title: str
    body: str


class RemoveSection(_Op):
    op: Literal["remove_section"]
    section_id: str


class MoveSection(_Op):
    op: Literal["move_section"]
    section_id: str
    after_id: str


class SetLayout(_Op):
    op: Literal["set_layout"]
    section_id: str
    layout: Literal["a", "b", "c"]


class SetImage(_Op):
    op: Literal["set_image"]
    section_id: str
    path: str


Op = Annotated[
    SetText
    | SetTheme
    | AddSection
    | RemoveSection
    | MoveSection
    | SetLayout
    | SetImage,
    Field(discriminator="op"),
]
_OPS = TypeAdapter(list[Op])

PLACEHOLDERS: dict[str, tuple[tuple[str, str], ...]] = {
    "features": tuple(
        (f"Feature {n}", "A short description of this feature.")
        for n in ("one", "two", "three")
    ),
    "gallery": tuple(
        (f"Project {n}", "") for n in ("one", "two", "three", "four", "five", "six")
    ),
    "testimonials": tuple(
        ("Customer name", "A short placeholder quote about the experience.")
        for _ in range(3)
    ),
    "pricing": (
        ("Starter", "Plan details go here."),
        ("Standard", "Plan details go here."),
        ("Premium", "Plan details go here."),
    ),
    "contact": (("Email", "Add your email address"),),
    "footer": (),
}


def new_section(kind: str, title: str, body: str, layout: str = "a") -> dict[str, Any]:
    """A section dict (no id yet) with generic placeholder items for its kind."""
    items = [{"title": t, "body": b} for t, b in PLACEHOLDERS.get(kind, ())]
    return {
        "kind": kind,
        "layout": layout,
        "title": title,
        "body": body,
        "items": items,
        "image": None,
    }


def parse_ops(raw: Any) -> list[Any]:
    if isinstance(raw, dict) and isinstance(raw.get("ops"), list):
        raw = raw["ops"]
    if not isinstance(raw, list) or len(raw) > MAX_OPS:
        raise SiteError("edit must be a list of at most 20 operations")
    try:
        return _OPS.validate_python(raw)
    except ValidationError as exc:
        err = exc.errors()[0]
        raise SiteError(f"invalid edit operation: {err['msg']}") from None


def _index(secs: list[dict[str, Any]], section_id: str) -> int:
    for n, s in enumerate(secs):
        if s["id"] == section_id:
            return n
    raise SiteError(f"there is no section {section_id}")


def apply_ops(
    spec: SiteSpec, raw_ops: Any, copy_image: Callable[[str], str] | None = None
) -> SiteSpec:
    """Return the edited spec, or raise SiteError (nothing is changed)."""
    ops = parse_ops(raw_ops)
    data = copy.deepcopy(spec.model_dump(mode="json"))
    secs: list[dict[str, Any]] = data["sections"]
    colors = False
    for op in ops:
        if isinstance(op, SetText):
            secs[_index(secs, op.section_id)][op.field] = op.value
        elif isinstance(op, SetTheme):
            value = op.value.strip()
            if op.key == "font":
                value = value.lower()
                if value not in FONTS:
                    raise SiteError(f"font must be one of {', '.join(FONTS)}")
            elif not HEX_RE.fullmatch(value):
                raise SiteError("colors must look like #RRGGBB")
            colors = colors or op.key in ("bg", "text")
            data["theme"][op.key] = value
        elif isinstance(op, AddSection):
            if op.kind not in MIDDLE_KINDS:
                raise SiteError(f"cannot add a {op.kind} section")
            at = _index(secs, op.after_id)
            nid = max(int(s["id"][1:]) for s in secs) + 1
            secs.insert(
                at + 1, {"id": f"s{nid}", **new_section(op.kind, op.title, op.body)}
            )
        elif isinstance(op, RemoveSection):
            secs.pop(_index(secs, op.section_id))
        elif isinstance(op, MoveSection):
            moving = secs.pop(_index(secs, op.section_id))
            if op.after_id == op.section_id:
                raise SiteError("a section cannot move after itself")
            secs.insert(_index(secs, op.after_id) + 1, moving)
        elif isinstance(op, SetLayout):
            secs[_index(secs, op.section_id)]["layout"] = op.layout
        elif isinstance(op, SetImage):
            at = _index(secs, op.section_id)
            if copy_image is None:
                raise SiteError("images cannot be added here")
            secs[at]["image"] = copy_image(op.path)
    if colors and not readable(data["theme"]["bg"], data["theme"]["text"]):
        raise SiteError("that would make the text hard to read")
    return validate_spec(data)


# ---------------------------------------------------------------- rule-based

COLORS = {
    "red": "#dc2626",
    "orange": "#ea580c",
    "amber": "#d97706",
    "yellow": "#eab308",
    "green": "#16a34a",
    "emerald": "#059669",
    "teal": "#0d9488",
    "cyan": "#0891b2",
    "blue": "#2563eb",
    "indigo": "#4f46e5",
    "purple": "#9333ea",
    "violet": "#7c3aed",
    "pink": "#db2777",
    "rose": "#e11d48",
    "white": "#ffffff",
    "black": "#0a0a0a",
    "gray": "#6b7280",
    "grey": "#6b7280",
    "gold": "#ca8a04",
    "navy": "#1e3a8a",
}
KIND_WORDS = {
    "hero": "hero",
    "header": "hero",
    "banner": "hero",
    "features": "features",
    "feature": "features",
    "benefits": "features",
    "services": "features",
    "gallery": "gallery",
    "photos": "gallery",
    "images": "gallery",
    "work": "gallery",
    "testimonials": "testimonials",
    "testimonial": "testimonials",
    "reviews": "testimonials",
    "quotes": "testimonials",
    "pricing": "pricing",
    "prices": "pricing",
    "plans": "pricing",
    "contact": "contact",
    "footer": "footer",
}
FONT_WORDS = {
    "serif": "serif",
    "sans": "sans",
    "sans-serif": "sans",
    "mono": "mono",
    "monospace": "mono",
    "display": "display",
}
TITLES = {
    "features": "Features",
    "gallery": "Gallery",
    "testimonials": "Testimonials",
    "pricing": "Pricing",
    "contact": "Contact",
}
DARK = ("#0b0b0f", "#f4f4f5")
LIGHT = ("#fafaf9", "#18181b")


def _hex(token: str) -> str | None:
    token = token.strip().lower()
    if re.fullmatch(r"#[0-9a-f]{3}", token):
        token = "#" + "".join(c * 2 for c in token[1:])
    return token if HEX_RE.fullmatch(token) else COLORS.get(token)


def _section(
    spec: SiteSpec, phrase: str, selected: str | None
) -> dict[str, Any] | None:
    """Find a section by "this", id (s3), kind word or title text."""
    secs = spec.model_dump(mode="json")["sections"]
    phrase = phrase.lower().strip()
    if selected and re.search(r"\b(this|it|selected|current)\b", phrase):
        return next((s for s in secs if s["id"] == selected), None)
    for word in re.findall(r"s\d+|[a-z-]+", phrase):
        for s in secs:
            if s["id"] == word or s["kind"] == KIND_WORDS.get(word):
                return s
    return next((s for s in secs if s["title"] and s["title"].lower() in phrase), None)


def _target(spec: SiteSpec, phrase: str | None, selected: str | None) -> dict[str, Any]:
    found = _section(spec, phrase, selected) if phrase else None
    if found is None and selected:
        found = _section(spec, "this", selected)
    if found is None and phrase:
        raise SiteError(f"I could not find a section called {phrase.strip()}")
    return found or spec.model_dump(mode="json")["sections"][0]


def rule_edit(
    spec: SiteSpec, instruction: str, selected: str | None = None
) -> list[dict[str, Any]] | None:
    """Ops for a simple instruction, [] if nothing needs to change, None if not understood.

    Raises SiteError when the request is understood but cannot be done.
    """
    text = " ".join(instruction.strip().rstrip(".!").split())
    low = text.lower()
    secs = spec.model_dump(mode="json")["sections"]
    theme = spec.theme

    m = re.match(
        r"(?:please )?(?:make|change|set|update|rewrite)\s+(?:the\s+)?(?:(?P<sec>[\w-]+(?: section)?)\s+)?"
        r"(?P<f>title|heading|headline|body|text|description|subtitle)\s+(?:(?:to|as|say|into)\s+)?"
        r"[\"“']?(?P<v>.+?)[\"”']?$",
        text,
        re.I,
    )
    if m and not re.match(r"colou?r\b", m["v"], re.I):
        sec = _target(spec, m["sec"], selected)
        field = (
            "title" if m["f"].lower() in ("title", "heading", "headline") else "body"
        )
        return [
            {"op": "set_text", "section_id": sec["id"], "field": field, "value": m["v"]}
        ]

    if re.search(r"\bfont\b", low):
        for word in re.findall(r"[a-z-]+", low):
            if word in FONT_WORDS:
                return [{"op": "set_theme", "key": "font", "value": FONT_WORDS[word]}]

    m = re.fullmatch(
        r"(?:please )?(?:make|turn|set)\s+(?:it|the (?:site|page|theme|website|background))?\s*(dark|light)(?: mode| theme)?",
        low,
    )
    if m:
        bg, tx = DARK if m[1] == "dark" else LIGHT
        return [
            {"op": "set_theme", "key": "bg", "value": bg},
            {"op": "set_theme", "key": "text", "value": tx},
        ]

    m = re.match(
        r"(?:please )?(?:make|change|set|turn|paint|use)\s+(?:it |the |my )?(?P<what>[\w ]*?)\s*(?:colou?r\s*)?(?:to |as |in )?(?P<v>#[0-9a-f]{3,6}|[a-z]+)$",
        low,
    )
    if (
        m
        and _hex(m["v"])
        and re.search(
            r"colou?r|theme|accent|primary|background|\bbg\b|text|site|page|\bit\b", low
        )
    ):
        value, what = _hex(m["v"]), m["what"]
        if re.search(r"background|\bbg\b", what):
            ops = [{"op": "set_theme", "key": "bg", "value": value}]
            if not readable(value, theme.text):
                best = max((DARK[1], LIGHT[1]), key=lambda c: contrast(value, c))
                ops.append({"op": "set_theme", "key": "text", "value": best})
            return ops
        key = "text" if re.search(r"\btext\b", what) else "primary"
        return [{"op": "set_theme", "key": key, "value": value}]

    m = re.fullmatch(
        r"(?:please )?(?:remove|delete|drop|hide|get rid of)\s+(?:the\s+)?(?P<s>.+?)(?:\s+section)?",
        low,
    )
    if m:
        return [
            {
                "op": "remove_section",
                "section_id": _target(spec, m["s"], selected)["id"],
            }
        ]

    m = re.fullmatch(
        r"(?:please )?add\s+(?:a|an|the)?\s*(?P<k>[\w ]+?)\s+section(?:\s+(?:after|below|under)\s+(?:the\s+)?(?P<a>.+?)(?:\s+section)?)?",
        low,
    )
    if m:
        kind = next((KIND_WORDS[w] for w in m["k"].split() if w in KIND_WORDS), None)
        if kind not in MIDDLE_KINDS:
            raise SiteError(
                "I can add features, gallery, testimonials, pricing or contact sections"
            )
        after = _target(spec, m["a"], None)["id"] if m["a"] else secs[-2]["id"]
        return [
            {
                "op": "add_section",
                "after_id": after,
                "kind": kind,
                "title": TITLES[kind],
                "body": "",
            }
        ]

    m = re.fullmatch(
        r"(?:please )?move\s+(?:the\s+)?(?P<a>.+?)(?:\s+section)?\s+(?P<d>above|before|below|after|under)\s+(?:the\s+)?(?P<b>.+?)(?:\s+section)?",
        low,
    )
    if m:
        a, b = _target(spec, m["a"], selected), _target(spec, m["b"], selected)
        if m["d"] in ("below", "after", "under"):
            after = b["id"]
        else:
            at = secs.index(b)
            if at == 0:
                raise SiteError("nothing can go above the hero")
            after = secs[at - 1]["id"]
        if after == a["id"]:
            return []
        return [{"op": "move_section", "section_id": a["id"], "after_id": after}]

    m = re.search(
        r"layout\s+(?:to\s+)?([abc])\b(?:\s+(?:for|on)\s+(?:the\s+)?(.+?)(?:\s+section)?)?$",
        low,
    )
    if m:
        return [
            {
                "op": "set_layout",
                "section_id": _target(spec, m[2], selected)["id"],
                "layout": m[1],
            }
        ]
    return None


__all__ = ["KINDS", "apply_ops", "new_section", "parse_ops", "rule_edit"]
