"""SiteSpec: the single source of truth for a generated website.

Frozen pydantic data like the other contracts. Edits never mutate a spec; they
build a new dict and re-validate it, so every stored version is valid.
"""

import re
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

SiteType = Literal["landing", "portfolio", "business"]
Kind = Literal[
    "hero", "features", "gallery", "testimonials", "pricing", "contact", "footer"
]
Layout = Literal["a", "b", "c"]
Font = Literal["serif", "sans", "mono", "display"]
KINDS: tuple[str, ...] = Kind.__args__  # type: ignore[attr-defined]
MIDDLE_KINDS = tuple(k for k in KINDS if k not in ("hero", "footer"))
SITE_TYPES: tuple[str, ...] = SiteType.__args__  # type: ignore[attr-defined]
FONTS: tuple[str, ...] = Font.__args__  # type: ignore[attr-defined]

SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
HEX_RE = re.compile(r"#[0-9a-fA-F]{6}")
IMAGE_RE = re.compile(r"assets/[A-Za-z0-9._-]{1,80}")
MIN_SECTIONS, MAX_SECTIONS = 3, 8

Hex = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]
Text = Annotated[str, StringConstraints(strict=True, max_length=2000)]


class SiteError(ValueError):
    """A refused spec, edit or path; the message is safe to show the user."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Item(_Frozen):
    title: Annotated[str, StringConstraints(strict=True, max_length=200)]
    body: Annotated[str, StringConstraints(strict=True, max_length=600)]


class Theme(_Frozen):
    primary: Hex
    bg: Hex
    text: Hex
    font: Font


class Section(_Frozen):
    id: Annotated[str, StringConstraints(pattern=r"^s[0-9]{1,4}$")]
    kind: Kind
    layout: Layout = "a"
    title: Annotated[str, StringConstraints(strict=True, max_length=200)]
    body: Text
    items: tuple[Item, ...] = Field(default=(), max_length=12)
    image: str | None = None

    @field_validator("image")
    @classmethod
    def _image(cls, value: str | None) -> str | None:
        if value is not None and not IMAGE_RE.fullmatch(value):
            raise ValueError("image must be a site asset")
        return value


class SiteSpec(_Frozen):
    id: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")]
    name: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=80)]
    type: SiteType
    theme: Theme
    sections: tuple[Section, ...]

    @model_validator(mode="after")
    def _shape(self) -> "SiteSpec":
        kinds = [s.kind for s in self.sections]
        if not MIN_SECTIONS <= len(kinds) <= MAX_SECTIONS:
            raise ValueError(f"a site needs {MIN_SECTIONS} to {MAX_SECTIONS} sections")
        if kinds[0] != "hero" or kinds[-1] != "footer":
            raise ValueError("the hero must stay first and the footer last")
        if kinds.count("hero") > 1 or kinds.count("footer") > 1:
            raise ValueError("a site has one hero and one footer")
        if len({s.id for s in self.sections}) != len(kinds):
            raise ValueError("section ids must be unique")
        return self


def validate_spec(data: dict[str, Any]) -> SiteSpec:
    try:
        return SiteSpec.model_validate(data)
    except ValidationError as exc:
        err = exc.errors()[0]
        where = ".".join(str(p) for p in err["loc"])
        raise SiteError(f"invalid site ({where}): {err['msg']}".strip()) from None


def _lum(color: str) -> float:
    def channel(i: int) -> float:
        c = int(color[i : i + 2], 16) / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5)


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio of two #RRGGBB colors."""
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def readable(bg: str, text: str) -> bool:
    return contrast(bg, text) >= 4.5


def _clip(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit].strip()


def from_model(raw: Any, slug: str) -> SiteSpec:
    """Build a valid spec from loose model JSON, repairing what is repairable.

    Raises SiteError if nothing usable (not an object, no sections).
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("sections"), list):
        raise SiteError("model reply is not a site spec")
    theme_in = raw.get("theme") if isinstance(raw.get("theme"), dict) else {}
    theme = {
        "primary": theme_in.get("primary"),
        "bg": theme_in.get("bg"),
        "text": theme_in.get("text"),
        "font": theme_in.get("font"),
    }
    for key, default in (
        ("primary", "#e8590c"),
        ("bg", "#0b0b0f"),
        ("text", "#f4f4f5"),
    ):
        if not (isinstance(theme[key], str) and HEX_RE.fullmatch(theme[key])):
            theme[key] = default
    if theme["font"] not in FONTS:
        theme["font"] = "sans"
    if not readable(theme["bg"], theme["text"]):
        theme["text"] = "#ffffff" if _lum(theme["bg"]) < 0.4 else "#111111"
    mid: list[dict[str, Any]] = []
    hero = footer = None
    for sec in raw["sections"]:
        if not isinstance(sec, dict) or sec.get("kind") not in KINDS:
            continue
        items = [
            {"title": _clip(i.get("title"), 200), "body": _clip(i.get("body"), 600)}
            for i in (sec.get("items") if isinstance(sec.get("items"), list) else [])[
                :12
            ]
            if isinstance(i, dict)
        ]
        built = {
            "kind": sec["kind"],
            "layout": sec.get("layout")
            if sec.get("layout") in ("a", "b", "c")
            else "a",
            "title": _clip(sec.get("title"), 200),
            "body": _clip(sec.get("body"), 2000),
            "items": items,
            "image": None,
        }
        if built["kind"] == "hero":
            hero = hero or built
        elif built["kind"] == "footer":
            footer = footer or built
        else:
            mid.append(built)
    if hero is None or footer is None:
        raise SiteError("model spec lacks a hero or footer")
    ordered = [hero, *mid[: MAX_SECTIONS - 2], footer]
    for n, sec in enumerate(ordered, 1):
        sec["id"] = f"s{n}"
    site_type = raw.get("type") if raw.get("type") in SITE_TYPES else "landing"
    return validate_spec(
        {
            "id": slug,
            "name": _clip(raw.get("name"), 80) or "My site",
            "type": site_type,
            "theme": theme,
            "sections": ordered,
        }
    )
