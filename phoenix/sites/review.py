# ruff: noqa: E501
"""ROBIN (copy) and LEVI (verification) stages of a site build.

Both are deterministic code, so they add no model prompt text: ROBIN tidies what AIZEN
wrote (word limits, stray markdown), LEVI checks readability and structure and repairs
what it can. Each returns (spec, one-line note) for the agent frame.
"""

import re

from phoenix.sites.spec import SiteSpec

TITLE_WORDS, BODY_WORDS, ITEM_BODY_WORDS = 8, 40, 24
DARK, LIGHT = "#0b0b0b", "#ffffff"


def _tidy(text: str, limit: int) -> str:
    text = re.sub(r"[*_`#]+", "", " ".join(str(text).split()))
    words = text.split(" ")
    if len(words) <= limit:
        return text
    cut = " ".join(words[:limit])
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[: end + 1] if end > len(cut) // 2 else cut.rstrip(",;:")).strip()


def polish_copy(spec: SiteSpec) -> tuple[SiteSpec, str]:
    """ROBIN: keep titles and bodies inside the approved limits, drop markdown."""
    data = spec.model_dump(mode="json")
    changed = 0
    for section in data["sections"]:
        for key, limit in (("title", TITLE_WORDS), ("body", BODY_WORDS)):
            new = _tidy(section.get(key) or "", limit)
            changed += new != (section.get(key) or "")
            section[key] = new
        for item in section.get("items") or []:
            for key, limit in (("title", TITLE_WORDS), ("body", ITEM_BODY_WORDS)):
                new = _tidy(item.get(key) or "", limit)
                changed += new != (item.get(key) or "")
                item[key] = new
    note = f"{changed} lines tidied" if changed else "copy already tight"
    return SiteSpec.model_validate(data), note


def _luminance(color: str) -> float:
    channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def verify(spec: SiteSpec, floor: float = 4.5) -> tuple[SiteSpec, str]:
    """LEVI: readable text and a visible accent (SiteSpec already pins hero first, footer last)."""
    data = spec.model_dump(mode="json")
    theme, fixes = data["theme"], []
    ratio = contrast(theme["text"], theme["bg"])
    if ratio < floor:
        best = max((DARK, LIGHT), key=lambda c: contrast(c, theme["bg"]))
        fixes.append(f"text contrast {ratio:.1f} to {contrast(best, theme['bg']):.1f}")
        theme["text"] = best
    accent = contrast(theme["primary"], theme["bg"])
    if accent < 2.0:
        best = max((DARK, LIGHT), key=lambda c: contrast(c, theme["bg"]))
        fixes.append(f"accent contrast {accent:.1f} to {contrast(best, theme['bg']):.1f}")
        theme["primary"] = best
    note = "fixed " + ", ".join(fixes) if fixes else f"all clear, contrast {ratio:.1f}:1"
    return SiteSpec.model_validate(data), note
