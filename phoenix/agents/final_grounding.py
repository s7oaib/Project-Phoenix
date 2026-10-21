"""PHX-118 (ADR-100): LEVI's deterministic grounding check for the final answer.

The final_verifier model sees each verified step output as a bare item with no
provenance, so it can reject a correct final answer that only repeats a value a
document step read and LEVI already verified (live: 9B rejected "AURORA" for
"no step output references project-notes.txt"). This check passes such an
answer without the model, and only when every condition holds:

- the candidate, stripped, is one line of at most MAX_GROUNDED_CHARS characters,
  is not JSON, and equals the stripped output of the plan's last step, which
  LEVI already verified (deterministic tool evidence first, then the model);
- that step is a document step whose read-only ``read_text`` call succeeded;
- the candidate occurs verbatim (case-sensitive) in the file content, bounded
  on both sides by the start or end, whitespace or sentence punctuation, so a
  part of a longer token (for example "AURORA" in "AURORA-7") never counts;
- the prompt-injection guard finds no signal in the file content or the
  candidate;
- PHX-118b (narrowed 2026-10-21): the candidate is, exactly, the value of a
  "label: value" ("label - value", "label = value") segment of the file (a
  line, or a sentence of a line), and that label shares a content word with
  the user's request (case-insensitive, stop words and file names removed,
  simple plural tolerance). An answer found only inside prose, for example an
  instruction sentence in the file, is never eligible.

Anything else, including every ungrounded or reworded answer, goes to the
unchanged final_verifier model check. The check grants nothing: LEVI's
all-checks verdict, INV-008 and the mission's other checks are unchanged.
"""

import json
import re
from dataclasses import dataclass
from hashlib import sha256

from phoenix.agents.tool_steps import ToolEvidence
from phoenix.agents.verifier import (
    VerificationCheck,
    VerificationDraft,
    VerificationRequest,
    VerificationResponder,
)
from phoenix.security.prompt_injection import (
    PromptInjectionGuard,
    UntrustedContentItem,
    UntrustedContentOrigin,
)

# Only runtime.py and tool_steps.py import phoenix.tools (PHX-046); these are
# the closed StrEnum values FilesystemOperation.READ_TEXT and
# ToolResultStatus.SUCCESS.
_READ_TEXT = "read_text"
_SUCCESS = "success"
GROUNDED_CHECK_ID = "deterministic.grounded_final"
MAX_GROUNDED_CHARS = 200
# Characters that may sit next to a grounded span: sentence punctuation and
# brackets, never token joiners such as "-", "_", "/" or "." inside a number.
_BOUNDARY = frozenset(",;:!?\"'()[]{}")
# PHX-118b: a file segment is a line, or a sentence of a line; its label is
# the text before the first ":", "=" or spaced " - ": a short name (at most
# MAX_LABEL_WORDS words of letters, digits, spaces, "_", "-", "/", "()"),
# never a clause of prose.
MAX_LABEL_WORDS = 5
_SENTENCE = re.compile(r"(?<=[.;!?])\s+")
_LABELLED = re.compile(
    r"^[-*\u2022\s]*(?P<label>[\w /()-]+?)\s*(?::|=|\s-\s)\s*(?P<value>.+)$"
)
_FILE_NAME = re.compile(r"[\w./\-]+\.[A-Za-z0-9]{1,8}\b")
_WORD = re.compile(r"[a-z0-9]+")
_STOP_WORDS = frozenset(
    "a an and answer are as at be by can could did do does file for from give"
    " has have how i in is it its just me my of on only or our please print"
    " reply respond return say show state tell than that the their them then"
    " there these this those to us was we were what when where which who whom"
    " whose why with word would write you your".split()
)


@dataclass(frozen=True)
class FinalGrounding:
    """The plan's last step: its LEVI-verified output and its tool evidence."""

    step_output: str
    evidence: ToolEvidence | None
    # PHX-118b: the user's request; its content words must name the label.
    request: str


def _clean(
    guard: PromptInjectionGuard, origin: UntrustedContentOrigin, text: str
) -> bool:
    item = UntrustedContentItem(
        content_id="grounding",
        origin=origin,
        source_id="grounding",
        field_name="content",
        text=text,
        text_sha256=sha256(text.encode("utf-8")).hexdigest(),
    )
    return not guard.assess(item).signals


def _bounded(content: str, span: str) -> bool:
    """span occurs in content with a boundary character on both sides."""

    def boundary(index: int) -> bool:
        if index < 0 or index >= len(content):
            return True
        char = content[index]
        return char.isspace() or char in _BOUNDARY

    def after(index: int) -> bool:
        # A full stop ends a sentence unless a token character follows it.
        end = index + len(span)
        if end < len(content) and content[end] == ".":
            return end + 1 >= len(content) or content[end + 1].isspace()
        return boundary(end)

    start = content.find(span)
    while start != -1:
        if boundary(start - 1) and after(start):
            return True
        start = content.find(span, start + 1)
    return False


def _content_words(text: str) -> frozenset[str]:
    words: set[str] = set()
    for word in _WORD.findall(_FILE_NAME.sub(" ", text).lower()):
        if word in _STOP_WORDS:
            continue
        if len(word) > 4 and word.endswith("ies"):
            word = word[:-3] + "y"
        elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        words.add(word)
    return frozenset(words)


def _labelled(content: str, answer: str, request: str) -> bool:
    """answer is the value of a label segment whose label the request names."""
    wanted = _content_words(request)
    for line in content.splitlines():
        for segment in _SENTENCE.split(line.strip()):
            match = _LABELLED.match(segment)
            if match is None or len(match["label"].split()) > MAX_LABEL_WORDS:
                continue
            value = match["value"].strip().rstrip(".,;!?").strip("\"'()[]{}")
            if value == answer and wanted & _content_words(match["label"]):
                return True
    return False


def _structured(text: str) -> bool:
    try:
        return isinstance(json.loads(text), (dict, list))
    except (ValueError, RecursionError):
        return False


def grounded(
    candidate: str, grounding: FinalGrounding, guard: PromptInjectionGuard
) -> bool:
    """True only when every PHX-118 condition holds; see the module docstring."""
    answer = candidate.strip()
    evidence = grounding.evidence
    if (
        not answer
        or len(answer) > MAX_GROUNDED_CHARS
        or "\n" in answer
        or "\r" in answer
        or answer != grounding.step_output.strip()
        or _structured(answer)
        or evidence is None
    ):
        return False
    result = evidence.result
    output = result.output
    if (
        result.operation != _READ_TEXT
        or evidence.call.operation != _READ_TEXT
        or result.status != _SUCCESS
        or output is None
    ):
        return False
    content = output.get("content")
    if (
        type(content) is not str
        or not _bounded(content, answer)
        or not _labelled(content, answer, grounding.request)
    ):
        return False
    return _clean(guard, UntrustedContentOrigin.TOOL_OUTPUT, content) and _clean(
        guard, UntrustedContentOrigin.MODEL_OUTPUT, answer
    )


class GroundedFinalVerificationResponder:
    """LEVI responder for the final answer: grounding check, else the model.

    A grounded answer passes on one deterministic check and the model is not
    consulted. Any other answer is verified by the inner responder unchanged.
    """

    def __init__(
        self,
        inner: VerificationResponder,
        guard: PromptInjectionGuard,
        grounding: FinalGrounding | None,
    ) -> None:
        self._inner = inner
        self._guard = guard
        self._grounding = grounding

    async def verify(self, request: VerificationRequest) -> VerificationDraft:
        grounding = self._grounding
        if grounding is not None and grounded(
            request.candidate_output, grounding, self._guard
        ):
            return VerificationDraft(
                verification_id=request.verification_id,
                checks=(
                    VerificationCheck(
                        check_id=GROUNDED_CHECK_ID,
                        criterion="Final answer repeats the verified last step"
                        " output, verbatim in the file PHOENIX read as the value"
                        " of a label the request names",
                        passed=True,
                        evidence_summary="Identical to the verified step output;"
                        " a bounded verbatim span of clean read_text content",
                    ),
                ),
            )
        return await self._inner.verify(request)
