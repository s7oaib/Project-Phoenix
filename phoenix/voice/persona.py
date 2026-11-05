"""DRAFT pending owner approval (PROMPTS_DRAFT_2026-11-05): persona text.

Spoken lines are code templates built from real data (no LLM). ``VOICE_RULE`` is
the one LLM-facing line; it is OFF unless ``PHOENIX_VOICE_RULE=1``.
"""

import os
from typing import Final

__all__ = (
    "ACK_LINES",
    "READY_LINE",
    "VOICE_RULE",
    "VOICE_RULE_ENV",
    "apply_voice_rule",
    "site_failed_line",
    "site_pending_line",
    "site_ready_line",
    "voice_rule_enabled",
)

VOICE_RULE: Final = (
    'Answer in one or two short sentences. Address the user as "boss". '
    "Give the result first. If you cannot do something, say so plainly. "
    "Never claim an action you did not perform."
)
VOICE_RULE_ENV: Final = "PHOENIX_VOICE_RULE"
ACK_LINES: Final = ("On it, boss.", "Right away, boss.", "Working on it, boss.")
READY_LINE: Final = "Yes boss, tell me."  # after "Stop, Phoenix": stopped, listening


def voice_rule_enabled() -> bool:
    return os.environ.get(VOICE_RULE_ENV, "").strip() == "1"


def apply_voice_rule(prompt: str) -> str:
    return f"{prompt}\n{VOICE_RULE}" if voice_rule_enabled() else prompt


def site_ready_line(sections: int | None) -> str:
    if sections is None:
        return "Boss, site ready."
    return f"Boss, site ready. {sections} section{'' if sections == 1 else 's'}."


def site_failed_line() -> str:
    return "Boss, the site build failed. Details are on screen."


def site_pending_line() -> str:
    return "Boss, the site is still building. It will appear on screen."
