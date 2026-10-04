"""PHOENIX authority and request lifetimes, without provider/tool integration."""

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal, Protocol, Self
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from phoenix.agents.registry import AgentRegistry, build_default_agent_registry

_Identifier = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
]
_Text = Annotated[str, StringConstraints(strict=True, min_length=1)]
_identifier = TypeAdapter(_Identifier)


class _Value(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )

    @field_validator("text", check_fields=False)
    @classmethod
    def meaningful_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Text must contain non-whitespace content")
        return value


class PhoenixRequest(_Value):
    request_id: _Identifier = Field(default_factory=lambda: str(uuid4()))
    text: _Text


class OrchestrationPath(StrEnum):
    DIRECT = "direct"
    COMPLEX = "complex"


_path = TypeAdapter(OrchestrationPath)


class DirectResponse(_Value):
    """Subordinate content only; no finalization or execution authority."""

    request_id: _Identifier
    text: _Text


class DirectResponder(Protocol):
    async def respond(self, request: PhoenixRequest) -> DirectResponse:
        """Return correlated content, or propagate failure/cancellation."""
        ...


class PhoenixResultKind(StrEnum):
    COMPLETED = "completed"
    PLANNING_REQUIRED = "planning_required"


class PhoenixResult(_Value):
    request_id: _Identifier
    orchestrator_id: Literal["phoenix"] = "phoenix"
    kind: PhoenixResultKind
    text: _Text | None = None

    @model_validator(mode="after")
    def consistent_content(self) -> Self:
        if self.kind == PhoenixResultKind.COMPLETED and self.text is None:
            raise ValueError("Completed results require text")
        if self.kind == PhoenixResultKind.PLANNING_REQUIRED and self.text is not None:
            raise ValueError("Planning-required results cannot contain completion text")
        return self


class CancellationStatus(StrEnum):
    ACCEPTED = "accepted"
    NOT_FOUND = "not_found"


class CancellationResult(_Value):
    """Acknowledgement of local cancellation, not proof remote work stopped."""

    request_id: _Identifier
    status: CancellationStatus


class PhoenixOrchestrationError(Exception):
    """An orchestration contract or lifetime requirement was violated."""


class DuplicateRequestError(PhoenixOrchestrationError):
    """The same request ID is already active on this orchestrator."""


@dataclass
class _ActiveRequest:
    task: asyncio.Task[object]
    cancellation_requested: bool = False


class PhoenixOrchestrator:
    """One process/event loop; caller tasks own operations, with no workers.

    The caller supplies the path and, for direct work, a neutral responder.
    One active operation may own each caller task; nested execution is rejected
    so cancelling one ID cannot cancel another operation on the same task.
    Cancellation is cooperative. Callers may impose their own deadlines.
    No registry role, alias, generated text or result grants permissions.
    """

    def __init__(self, registry: AgentRegistry | None = None) -> None:
        store = build_default_agent_registry() if registry is None else registry
        AgentRegistry.model_validate(store.model_dump()).require("phoenix")
        self._active: dict[str, _ActiveRequest] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def orchestrator_id(self) -> Literal["phoenix"]:
        return "phoenix"

    @property
    def active_request_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._active))

    def _bind_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise PhoenixOrchestrationError(
                "Orchestrator belongs to another event loop"
            )
        self._loop = loop

    async def execute(
        self,
        request: PhoenixRequest,
        path: OrchestrationPath,
        *,
        direct_responder: DirectResponder | None = None,
    ) -> PhoenixResult:
        self._bind_loop()
        if not isinstance(request, PhoenixRequest):
            raise PhoenixOrchestrationError("Expected a PhoenixRequest")
        request = PhoenixRequest.model_validate(request)
        directive = _path.validate_python(path)
        if request.request_id in self._active:
            raise DuplicateRequestError("Request ID is already active")
        task = asyncio.current_task()
        if task is None:
            raise PhoenixOrchestrationError("Execution requires a caller task")
        if any(active.task is task for active in self._active.values()):
            raise PhoenixOrchestrationError(
                "Caller task already owns an active request"
            )
        operation = _ActiveRequest(task)
        self._active[request.request_id] = operation
        try:
            if directive == OrchestrationPath.COMPLEX:
                return PhoenixResult(
                    request_id=request.request_id,
                    kind=PhoenixResultKind.PLANNING_REQUIRED,
                )
            if direct_responder is None:
                raise PhoenixOrchestrationError("Direct execution requires a responder")
            response = await direct_responder.respond(request)
            # A collaborator cannot suppress cancellation and finalize success.
            if operation.cancellation_requested or task.cancelling():
                raise asyncio.CancelledError
            if type(response) is not DirectResponse:
                raise PhoenixOrchestrationError("Expected a subordinate DirectResponse")
            try:
                response = DirectResponse.model_validate(response)
            except ValidationError:
                raise PhoenixOrchestrationError("Invalid direct response") from None
            if response.request_id != request.request_id:
                raise PhoenixOrchestrationError("Direct response request ID mismatch")
            return PhoenixResult(
                request_id=request.request_id,
                kind=PhoenixResultKind.COMPLETED,
                text=response.text,
            )
        finally:
            del self._active[request.request_id]

    async def cancel(self, request_id: str) -> CancellationResult:
        self._bind_loop()
        identity = _identifier.validate_python(request_id)
        operation = self._active.get(identity)
        status = CancellationStatus.NOT_FOUND
        if operation is not None:
            operation.cancellation_requested = True
            operation.task.cancel()
            status = CancellationStatus.ACCEPTED
        return CancellationResult(request_id=identity, status=status)
