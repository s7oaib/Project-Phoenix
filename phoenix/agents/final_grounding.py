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
  candidate.

Anything else, including every ungrounded or reworded answer, goes to the
unchanged final_verifier model check. The check grants nothing: LEVI's
all-checks verdict, INV-008 and the mission's other checks are unchanged.
"""

import json
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


@dataclass(frozen=True)
class FinalGrounding:
    """The plan's last step: its LEVI-verified output and its tool evidence."""

    step_output: str
    evidence: ToolEvidence | None


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
    if type(content) is not str or not _bounded(content, answer):
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
                        " output, verbatim in the file PHOENIX read",
                        passed=True,
                        evidence_summary="Identical to the verified step output;"
                        " a bounded verbatim span of clean read_text content",
                    ),
                ),
            )
        return await self._inner.verify(request)
