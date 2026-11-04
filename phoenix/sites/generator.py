# ruff: noqa: E501
"""Spec generation and edit planning: model first, deterministic fallback after.

The model is any ``async (system, user) -> text``; ``provider_model_fn`` adapts
the existing ModelProvider abstraction. Building never fails visibly: one retry
on a bad reply, then a spec from prompt keywords.
"""

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from phoenix.models.provider import (
    GenerationRequest,
    MessageRole,
    ModelMessage,
    ModelProvider,
)
from phoenix.sites.editor import apply_ops, new_section, rule_edit
from phoenix.sites.prompts import SITE_EDIT_SYSTEM, SITE_SPEC_SYSTEM
from phoenix.sites.spec import SiteError, SiteSpec, from_model, validate_spec

ModelFn = Callable[[str, str], Awaitable[str]]
SITE_MODEL = "qwen3.5:4b"
MODEL_TIMEOUT_SECONDS = 60.0

PALETTES = {  # primary, bg, text, font
    "landing": ("#f0642a", "#0b0b0f", "#f4f4f5", "display"),
    "portfolio": ("#b4440f", "#f6f3ee", "#1a1a1a", "serif"),
    "business": ("#2563eb", "#ffffff", "#0f172a", "sans"),
}
TYPE_WORDS = {
    "portfolio": r"portfolio|photograph|designer|artist|illustrator|resume|\bcv\b|freelanc",
    "business": r"business|company|agency|shop|store|restaurant|cafe|clinic|studio|consult|firm|bakery|salon",
}
SECTIONS = {
    "landing": ("features", "testimonials", "pricing", "contact"),
    "portfolio": ("gallery", "features", "testimonials", "contact"),
    "business": ("features", "pricing", "testimonials", "contact"),
}
SECTION_TITLES = {
    "features": "What you get",
    "gallery": "Selected work",
    "testimonials": "Kind words",
    "pricing": "Simple plans",
    "contact": "Get in touch",
}


def provider_model_fn(providers: Sequence[ModelProvider]) -> ModelFn | None:
    """Adapt the first provider (the think=False structured twin) to a ModelFn.

    ponytail: GenerationRequest has no format/keep_alive fields, so "format json" and
    keep_alive 30m need a provider-side change; the prompts already demand bare JSON.
    """
    if not providers:
        return None
    provider = providers[0]

    async def call(system: str, user: str) -> str:
        request = GenerationRequest(
            model_id=SITE_MODEL,
            messages=(
                ModelMessage(role=MessageRole.SYSTEM, text=system),
                ModelMessage(role=MessageRole.USER, text=user),
            ),
            temperature=0.2,
            max_output_tokens=2000,
        )
        return (
            await asyncio.wait_for(provider.generate(request), MODEL_TIMEOUT_SECONDS)
        ).text

    return call


def _close_brackets(text: str) -> str:
    """Fix the bracket slips qwen3.5:4b makes: a dropped final }, or a } where ]}
    belongs. A closer that does not match first closes whatever is still open."""
    out: list[str] = []
    stack: list[str] = []
    in_str = escaped = False
    for ch in text:
        if in_str:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
            out.append(ch)
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
            out.append(ch)
        elif ch in "}]":
            while stack and stack[-1] != ch:
                out.append(stack.pop())
            if stack:
                stack.pop()
                out.append(ch)  # a stray closer with nothing open is dropped
        else:
            out.append(ch)
    return "".join(out) + ('"' if in_str else "") + "".join(reversed(stack))


def parse_json(text: str) -> Any:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except ValueError:
        starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
        if not starts:
            raise
        start = min(starts)
        try:
            return json.loads(_close_brackets(text[start:]))
        except ValueError:
            return json.loads(
                text[start : text.rfind("}" if text[start] == "{" else "]") + 1]
            )


def fallback_spec(prompt: str, slug: str) -> SiteSpec:
    """A complete, valid site from prompt keywords alone."""
    low = prompt.lower()
    kind = next((t for t, rx in TYPE_WORDS.items() if re.search(rx, low)), "landing")
    primary, bg, text, font = PALETTES[kind]
    for word, color in (
        ("blue", "#2563eb"),
        ("green", "#16a34a"),
        ("red", "#dc2626"),
        ("purple", "#9333ea"),
        ("pink", "#db2777"),
        ("orange", "#ea580c"),
    ):
        if word in low:
            primary = color
    if "dark" in low and kind != "landing":
        bg, text = "#0b0b0f", "#f4f4f5"
    elif "light" in low and kind == "landing":
        bg, text = "#fafaf9", "#18181b"
    found = re.search(
        r"\b(?:for|about|called|named)\s+(?:an?\s+|my\s+|the\s+|our\s+)?([^,.]{3,40})",
        prompt,
        re.I,
    )
    subject = " ".join((found[1] if found else prompt).split()[:4]).strip() or "My site"
    name = subject.title()[:80]
    offset = sum(map(ord, prompt)) % 3
    layouts = "abc"
    secs = [
        new_section(
            "hero",
            name,
            f"A clean, modern site for {subject.lower()}.",
            layouts[offset],
        )
    ]
    for n, k in enumerate(SECTIONS[kind]):
        secs.append(
            new_section(k, SECTION_TITLES[k], "", layouts[(offset + n + 1) % 3])
        )
    secs.append(new_section("footer", name, "Made with PHOENIX."))
    for n, s in enumerate(secs, 1):
        s["id"] = f"s{n}"
    return validate_spec(
        {
            "id": slug,
            "name": name,
            "type": kind,
            "theme": {"primary": primary, "bg": bg, "text": text, "font": font},
            "sections": secs,
        }
    )


async def build_spec(prompt: str, slug: str, model: ModelFn | None) -> SiteSpec:
    prompt = prompt.strip()[:500]
    for _ in range(2 if model else 0):
        try:
            return from_model(parse_json(await model(SITE_SPEC_SYSTEM, prompt)), slug)  # type: ignore[misc]
        except Exception:  # bad JSON, bad spec, provider down or timeout: retry once
            continue
    return fallback_spec(prompt, slug)


def _brief(spec: SiteSpec) -> dict[str, Any]:
    data = spec.model_dump(mode="json")
    return {
        "theme": data["theme"],
        "sections": [
            {k: s[k] for k in ("id", "kind", "layout", "title", "body")}
            for s in data["sections"]
        ],
    }


async def edit_spec(
    spec: SiteSpec,
    instruction: str,
    section_id: str | None,
    model: ModelFn | None,
    copy_image: Callable[[str], str] | None = None,
) -> tuple[SiteSpec | None, str]:
    """(new spec, message). New spec is None when nothing changed (message says why)."""
    problem = ""
    try:  # deterministic rules first: exact and instant (the 4B model misplaces moves)
        ops = rule_edit(spec, instruction, section_id)
        if ops:
            return apply_ops(spec, ops, copy_image), "Done."
        if ops == []:
            return None, "Nothing to change."
    except SiteError as exc:
        problem = str(exc)  # a rule matched but cannot apply: let the model try
    if model is not None:
        user = json.dumps(
            {
                "spec": _brief(spec),
                "instruction": instruction[:500],
                "selected_section_id": section_id,
            }
        )
        try:
            ops = parse_json(await model(SITE_EDIT_SYSTEM, user))
            if ops not in ([], {}):
                return apply_ops(spec, ops, copy_image), "Done."
        except SiteError as exc:
            problem = str(exc)
        except Exception:  # model down, timeout or bad JSON
            pass
    return None, problem or "I could not turn that into an edit."
