"""Owned neutral responder behind the PHX-055 always-untrusted JSON boundary.

No model adapter, action, history or automatic tool path. Callers own deadlines
and application-wide scheduling. Cancellation/close require a cooperative
responder; acknowledgement does not prove remote computation stopped.
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Self, cast

from pydantic import AwareDatetime, field_validator, model_validator

from phoenix.models.provider import CancellationResult, CancellationStatus
from phoenix.security.prompt_injection import (
    GuardedPrompt,
    PromptBoundaryRequest,
    PromptInjectionError,
    PromptInjectionErrorCode,
    PromptInjectionGuard,
    UntrustedContentItem,
    UntrustedContentOrigin,
    _Contract,
    _enum,
    _exact,
    _identifier,
    _stored,
)
from phoenix.web.research import (
    ResearchClaimDraft,
    ResearchContentKind,
    ResearchSourceEvidence,
    ResearchSynthesisDraft,
    ResearchSynthesisRequest,
    ResearchSynthesizer,
)

__all__ = (
    "GuardedResearchRequest",
    "GuardedResearchResponder",
    "GuardedResearchSynthesizer",
)


class GuardedResearchRequest(_Contract):
    research_id: str
    responder_id: str
    as_of: AwareDatetime
    allowed_source_ids: tuple[str, ...]
    guarded_prompt: GuardedPrompt

    @field_validator("research_id", "responder_id", mode="before")
    @classmethod
    def identity(cls, value: object) -> str:
        return _identifier(value)

    @field_validator("as_of", mode="before")
    @classmethod
    def timestamp(cls, value: object) -> object:
        if type(value) not in (str, datetime):
            raise ValueError("invalid_input")
        return value

    @field_validator("as_of")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @field_validator("allowed_source_ids", mode="before")
    @classmethod
    def allowed(cls, value: object) -> tuple[str, ...]:
        if type(value) not in (tuple, list):
            raise ValueError("invalid_input")
        values = cast(Sequence[object], value)
        if not 1 <= len(values) <= 8:
            raise ValueError("invalid_input")
        ids = tuple(_identifier(item) for item in values)
        if len(set(ids)) != len(ids):
            raise ValueError("invalid_input")
        return ids

    @field_validator("guarded_prompt", mode="before")
    @classmethod
    def prompt_snapshot(cls, value: object) -> GuardedPrompt:
        return GuardedPrompt.model_validate(value)

    @model_validator(mode="after")
    def correlation(self) -> Self:
        if self.guarded_prompt.boundary_id != self.research_id or any(
            item.source_id not in self.allowed_source_ids
            for item in self.guarded_prompt.items
        ):
            raise PromptInjectionError(PromptInjectionErrorCode.INVALID_INPUT) from None
        return self


class GuardedResearchResponder(ABC):
    """Future adapters MUST use guarded_prompt.render_text() as model input.

    One owned responder's lifetime belongs to its GuardedResearchSynthesizer.
    Output remains untrusted and must not be interpreted as action/permission.
    """

    def __init__(self, responder_id: str) -> None:
        try:
            self._responder_id = _identifier(responder_id)
        except Exception:
            raise PromptInjectionError(PromptInjectionErrorCode.INVALID_INPUT) from None

    @property
    def responder_id(self) -> str:
        return self._responder_id

    @property
    @abstractmethod
    def active_research_ids(self) -> tuple[str, ...]:
        """Sorted immutable active IDs, not task objects or permission evidence."""

    @abstractmethod
    async def respond(self, request: GuardedResearchRequest) -> ResearchSynthesisDraft:
        """Interpret only the guarded boundary; no concrete model is supplied here."""

    @abstractmethod
    async def cancel(self, research_id: str) -> CancellationResult:
        """Request cancellation only for this exact research ID."""

    @abstractmethod
    async def aclose(self) -> None:
        """Release this owned responder's resources cooperatively."""


_SOURCE_FIELDS = (
    "requested_url",
    "final_url",
    "title",
    "source_name",
    "date_text",
    "source_ref",
    "content",
)


def _items(source: ResearchSourceEvidence) -> tuple[UntrustedContentItem, ...]:
    result = []
    for field in _SOURCE_FIELDS:
        text = getattr(source, field)
        if text is None:
            continue
        page = source.content_kind is ResearchContentKind.PAGE_TEXT and field in (
            "content",
            "final_url",
        )
        identity = json.dumps(
            [source.source_id, field], ensure_ascii=True, separators=(",", ":")
        )
        result.append(
            UntrustedContentItem(
                content_id="content-" + sha256(identity.encode("utf-8")).hexdigest(),
                origin=UntrustedContentOrigin.WEB_PAGE
                if page
                else UntrustedContentOrigin.WEB_SEARCH,
                source_id=source.source_id,
                field_name=field,
                text=text,
                text_sha256=sha256(text.encode("utf-8")).hexdigest(),
            )
        )
    return tuple(result)


@dataclass(slots=True)
class _Active:
    research_id: str
    task: asyncio.Task[object]
    cancelled: bool = False


class GuardedResearchSynthesizer(ResearchSynthesizer):
    """One caller synthesis, no worker/queue. Owns and closes responder once.

    Closed synthesize/cancel raise closed. aclose is idempotent; concurrent close
    callers wait for the first close and see its safe cleanup error if any.
    The adapter becomes closed before awaiting cleanup, even when cleanup fails.
    No automatic retry or global model shutdown. Zero sources reject invalid_input;
    PHX-054 already skips synthesis for zero-source reports. Metadata can cause
    boundary_overflow even when upstream content alone fits its separate budget.
    """

    def __init__(
        self, guard: PromptInjectionGuard, responder: GuardedResearchResponder
    ) -> None:
        try:
            if type(guard) is not PromptInjectionGuard or not isinstance(
                responder, GuardedResearchResponder
            ):
                raise ValueError("invalid_input")
            identity = _identifier(responder.responder_id)
            if identity != responder.responder_id:
                raise ValueError("invalid_input")
        except Exception:
            raise PromptInjectionError(PromptInjectionErrorCode.INVALID_INPUT) from None
        super().__init__(identity)
        self._guard = guard
        self._responder = responder
        self._active: _Active | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed = False
        self._close_done: asyncio.Event | None = None
        self._close_owner: asyncio.Task[object] | None = None
        self._close_failed = False

    @property
    def active_research_ids(self) -> tuple[str, ...]:
        return () if self._active is None else (self._active.research_id,)

    @property
    def closed(self) -> bool:
        return self._closed

    def _bind_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise PromptInjectionError(PromptInjectionErrorCode.WRONG_EVENT_LOOP)

    @staticmethod
    def _check_cancel(active: _Active) -> None:
        if active.cancelled or active.task.cancelling():
            raise asyncio.CancelledError

    async def synthesize(
        self, request: ResearchSynthesisRequest
    ) -> ResearchSynthesisDraft:
        if self._closed:
            raise PromptInjectionError(PromptInjectionErrorCode.CLOSED)
        try:
            data = _stored(request, ResearchSynthesisRequest)
            sources = data.get("sources")
            if (
                type(sources) not in (list, tuple)
                or not 1 <= len(cast(Sequence[object], sources)) <= 8
            ):
                raise ValueError("invalid_input")
            data["sources"] = tuple(
                _exact(item, ResearchSourceEvidence)
                for item in cast(Sequence[object], sources)
            )
            accepted = ResearchSynthesisRequest.model_validate(data)
            if (
                not accepted.sources
                or self._responder.responder_id != self.synthesizer_id
            ):
                raise ValueError("invalid_input")
        except Exception:
            raise PromptInjectionError(PromptInjectionErrorCode.INVALID_INPUT) from None
        if self._active is not None:
            raise PromptInjectionError(PromptInjectionErrorCode.BUSY)
        self._bind_loop()
        task = asyncio.current_task()
        assert task is not None
        active = _Active(accepted.research_id, task)
        self._active = active
        try:
            prompt = self._guard.guard(
                PromptBoundaryRequest(
                    boundary_id=accepted.research_id,
                    user_task=accepted.query_text,
                    items=tuple(
                        item for source in accepted.sources for item in _items(source)
                    ),
                )
            )
            guarded = GuardedResearchRequest(
                research_id=accepted.research_id,
                responder_id=self.synthesizer_id,
                as_of=accepted.as_of,
                allowed_source_ids=tuple(
                    source.source_id for source in accepted.sources
                ),
                guarded_prompt=prompt,
            )
            self._check_cancel(active)
            try:
                raw = await self._responder.respond(guarded)
            except Exception:
                self._check_cancel(active)
                raise PromptInjectionError(
                    PromptInjectionErrorCode.INVALID_RESPONSE
                ) from None
            self._check_cancel(active)
            try:
                data = _stored(raw, ResearchSynthesisDraft)
                claims = data.get("claims")
                if (
                    type(claims) not in (tuple, list)
                    or len(cast(Sequence[object], claims)) > 50
                ):
                    raise ValueError("invalid_response")
                data["claims"] = tuple(
                    _exact(item, ResearchClaimDraft)
                    for item in cast(Sequence[object], claims)
                )
                draft = ResearchSynthesisDraft.model_validate(data)
                if (draft.research_id, draft.synthesizer_id) != (
                    accepted.research_id,
                    self.synthesizer_id,
                ) or any(
                    source_id not in guarded.allowed_source_ids
                    for claim in draft.claims
                    for source_id in claim.supporting_source_ids
                ):
                    raise ValueError("invalid_response")
            except Exception:
                raise PromptInjectionError(
                    PromptInjectionErrorCode.INVALID_RESPONSE
                ) from None
            self._check_cancel(active)
            return draft
        finally:
            self._active = None

    async def _cancel_active(self, active: _Active) -> None:
        if active.cancelled:
            return
        active.cancelled = True
        try:
            raw = await self._responder.cancel(active.research_id)
            data = _stored(raw, CancellationResult)
            data["operation_id"] = _identifier(data.get("operation_id"))
            data["provider_id"] = _identifier(data.get("provider_id"))
            _enum(data.get("status"), CancellationStatus)
            result = CancellationResult.model_validate(data)
            if (result.operation_id, result.provider_id) != (
                active.research_id,
                self.synthesizer_id,
            ):
                raise ValueError("cleanup_failure")
        except Exception:
            raise PromptInjectionError(
                PromptInjectionErrorCode.CLEANUP_FAILURE
            ) from None
        finally:
            # A delayed acknowledgement must not cancel unrelated caller work
            # after this active operation was already cleaned up.
            if (
                self._active is active
                and active.task is not asyncio.current_task()
                and not active.task.done()
            ):
                active.task.cancel()

    async def cancel(self, research_id: str) -> CancellationResult:
        if self._closed:
            raise PromptInjectionError(PromptInjectionErrorCode.CLOSED)
        try:
            identity = _identifier(research_id)
        except Exception:
            raise PromptInjectionError(PromptInjectionErrorCode.INVALID_INPUT) from None
        self._bind_loop()
        active = self._active
        status = CancellationStatus.NOT_FOUND
        if active is not None and active.research_id == identity:
            await self._cancel_active(active)
            status = CancellationStatus.ACCEPTED
        return CancellationResult(
            operation_id=identity, provider_id=self.synthesizer_id, status=status
        )

    async def aclose(self) -> None:
        self._bind_loop()
        if self._close_done is not None:
            if self._close_owner is not asyncio.current_task():
                await self._close_done.wait()
            if self._close_failed:
                raise PromptInjectionError(PromptInjectionErrorCode.CLEANUP_FAILURE)
            return
        self._closed = True
        self._close_done = asyncio.Event()
        self._close_owner = asyncio.current_task()
        try:
            try:
                if self._active is not None:
                    await self._cancel_active(self._active)
            finally:
                await self._responder.aclose()
        except asyncio.CancelledError:
            self._close_failed = True
            raise
        except Exception:
            self._close_failed = True
            raise PromptInjectionError(
                PromptInjectionErrorCode.CLEANUP_FAILURE
            ) from None
        finally:
            self._close_owner = None
            self._close_done.set()
