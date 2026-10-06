"""Advisory vision over explicit evidence; never capture, authorize or execute.

Regions and sufficient flags are untrusted interpretation, not verification.
One caller task owns a bounded loop; cancellation flags survive lower-layer
suppression. Trusted orchestration separately owns frame admission and actions.
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, Any, Self, TypeVar, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    StringConstraints,
    field_validator,
    model_validator,
)

from phoenix.models.provider import CancellationResult, CancellationStatus
from phoenix.tools.screenshot import ScreenshotFrame

__all__ = (
    "VisionRegion",
    "VisionInterpretRequest",
    "VisionInterpretResponse",
    "VisionInterpreter",
    "VisionError",
    "VisionErrorCode",
    "VisionLoopRequest",
    "VisionLoopResult",
    "VisionLoopService",
)

_Id = Annotated[str, StringConstraints(strict=True, min_length=1)]
_Model = TypeVar("_Model", bound=BaseModel)


class VisionErrorCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    INVALID_RESPONSE = "invalid_response"
    VISION_UNSUPPORTED = "vision_unsupported"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    BUSY = "busy"
    CLOSED = "closed"
    WRONG_EVENT_LOOP = "wrong_event_loop"
    CLEANUP_FAILURE = "cleanup_failure"


class VisionError(Exception):
    def __init__(self, code: VisionErrorCode) -> None:
        self.code = code
        super().__init__(f"Vision boundary failure: {code.value}")


def _text(
    value: object, maximum: int | None = None, *, identifier: bool = False
) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError("Expected ordinary meaningful text")
    if identifier and any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value):
        raise ValueError("Expected control-free identifier")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValueError("Expected valid Unicode") from None
    result = value.strip() if identifier else value
    if maximum is not None and len(result) > maximum:
        raise ValueError("Text exceeds bound")
    return result


def _stored(value: BaseModel, contract: type[BaseModel]) -> dict[str, object]:
    if type(value) is not contract:
        raise ValueError("Expected exact vision contract")
    extra = value.__pydantic_extra__
    if extra is not None and type(extra) is not dict:
        raise ValueError("Invalid stored extras")
    return {**vars(value), **(extra or {})}


def _validated(value: object, contract: type[_Model]) -> _Model:
    if not isinstance(value, BaseModel):
        raise ValueError("Expected exact vision contract")
    return contract.model_validate(_stored(value, contract))


class _Contract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
        validate_default=True,
        hide_input_in_errors=True,
    )

    @model_validator(mode="wrap")
    @classmethod
    def snapshot(cls, value: object, handler: ModelWrapValidatorHandler[Self]) -> Self:
        return handler(_stored(value, cls) if isinstance(value, BaseModel) else value)

    @field_validator(
        "operation_id",
        "provider_id",
        "model_id",
        "frame_id",
        "loop_id",
        mode="before",
        check_fields=False,
    )
    @classmethod
    def identifiers(cls, value: object) -> str:
        return _text(value, identifier=True)

    @field_validator("query_text", mode="before", check_fields=False)
    @classmethod
    def query(cls, value: object) -> str:
        return _text(value, 4096)


class VisionRegion(_Contract):
    label: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=256)]
    x: Annotated[int, Field(strict=True, ge=0)]
    y: Annotated[int, Field(strict=True, ge=0)]
    width: Annotated[int, Field(strict=True, ge=1, le=4096)]
    height: Annotated[int, Field(strict=True, ge=1, le=4096)]
    confidence: float = Field(strict=True, ge=0, le=1, allow_inf_nan=False)

    @field_validator("label", mode="before")
    @classmethod
    def meaningful_label(cls, value: object) -> str:
        return _text(value, 256)

    @field_validator("confidence", mode="before")
    @classmethod
    def ordinary_float(cls, value: object) -> object:
        if type(value) is not float:
            raise ValueError("Expected ordinary finite float")
        return value

    def validate_frame(self, frame: ScreenshotFrame) -> None:
        if self.x + self.width > frame.width or self.y + self.height > frame.height:
            raise ValueError("Region exceeds correlated frame")


class VisionInterpretRequest(_Contract):
    operation_id: _Id
    model_id: _Id
    query_text: str = Field(strict=True, repr=False)
    frame: ScreenshotFrame = Field(repr=False)
    iteration: Annotated[int, Field(strict=True, ge=1, le=3)]

    @field_validator("frame", mode="before")
    @classmethod
    def frame_snapshot(cls, value: object) -> ScreenshotFrame:
        return (
            _validated(value, ScreenshotFrame)
            if isinstance(value, BaseModel)
            else ScreenshotFrame.model_validate(value)
        )


class VisionInterpretResponse(_Contract):
    operation_id: _Id
    provider_id: _Id
    model_id: _Id
    frame_id: _Id
    summary: str = Field(strict=True, max_length=8192, repr=False)
    regions: tuple[VisionRegion, ...] = Field(max_length=64, repr=False)
    sufficient: bool = Field(strict=True)

    @field_validator("summary", mode="before")
    @classmethod
    def summary_text(cls, value: object) -> str:
        if type(value) is not str:
            raise ValueError("Expected ordinary summary text")
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise ValueError("Expected valid Unicode summary") from None
        return value

    @field_validator("regions", mode="before")
    @classmethod
    def region_snapshot(cls, value: object) -> tuple[VisionRegion, ...]:
        if type(value) not in (tuple, list) or len(cast(Sequence[object], value)) > 64:
            raise ValueError("Expected bounded ordinary region sequence")
        return tuple(
            _validated(region, VisionRegion)
            if isinstance(region, BaseModel)
            else VisionRegion.model_validate(region)
            for region in cast(Sequence[object], value)
        )

    def validate_request(
        self, request: VisionInterpretRequest, provider_id: str
    ) -> None:
        if (self.operation_id, self.provider_id, self.model_id, self.frame_id) != (
            request.operation_id,
            provider_id,
            request.model_id,
            request.frame.frame_id,
        ):
            raise ValueError("Vision response correlation disagrees")
        for region in self.regions:
            region.validate_frame(request.frame)


class VisionInterpreter(ABC):
    @property
    @abstractmethod
    def provider_id(self) -> str: ...

    @abstractmethod
    async def interpret(
        self, request: VisionInterpretRequest
    ) -> VisionInterpretResponse: ...

    @abstractmethod
    async def cancel(self, operation_id: str) -> CancellationResult: ...

    @abstractmethod
    async def aclose(self) -> None: ...


class VisionLoopRequest(_Contract):
    loop_id: _Id
    provider_id: _Id
    model_id: _Id
    query_text: str = Field(strict=True, repr=False)
    frames: tuple[ScreenshotFrame, ...] = Field(min_length=1, max_length=3, repr=False)
    max_iterations: Annotated[int, Field(strict=True, ge=1, le=3)]

    @field_validator("frames", mode="before")
    @classmethod
    def frames_snapshot(cls, value: object) -> tuple[ScreenshotFrame, ...]:
        if (
            type(value) not in (tuple, list)
            or not 1 <= len(cast(Sequence[object], value)) <= 3
        ):
            raise ValueError("Expected finite ordinary frame sequence")
        return tuple(
            _validated(frame, ScreenshotFrame)
            if isinstance(frame, BaseModel)
            else ScreenshotFrame.model_validate(frame)
            for frame in cast(Sequence[object], value)
        )

    @model_validator(mode="after")
    def sequence(self) -> Self:
        if self.max_iterations > len(self.frames):
            raise ValueError("Iteration budget exceeds supplied frames")
        if (
            len({frame.frame_id for frame in self.frames}) != len(self.frames)
            or len(
                {
                    (frame.hwnd, frame.process_id, frame.class_name)
                    for frame in self.frames
                }
            )
            != 1
            or any(
                first.captured_at > second.captured_at
                for first, second in zip(self.frames, self.frames[1:])
            )
        ):
            raise ValueError("Invalid frame identity/order")
        return self


def _operation_id(loop_id: str, iteration: int) -> str:
    # Length-delimited canonical input avoids caller separator collisions.
    return (
        "vision."
        + sha256(
            json.dumps([loop_id, iteration], separators=(",", ":")).encode()
        ).hexdigest()
    )


class VisionLoopResult(_Contract):
    loop_id: _Id
    provider_id: _Id
    model_id: _Id
    frame_ids: tuple[_Id, ...] = Field(min_length=1, max_length=3)
    interpretations: tuple[VisionInterpretResponse, ...] = Field(
        min_length=1, max_length=3, repr=False
    )
    iterations_used: Annotated[int, Field(strict=True, ge=1, le=3)]
    sufficient: bool = Field(strict=True)
    exhausted: bool = Field(strict=True)
    advisory_only: bool = Field(default=True, strict=True)

    @field_validator("frame_ids", mode="before")
    @classmethod
    def frame_identifiers(cls, value: object) -> tuple[str, ...]:
        if type(value) not in (list, tuple):
            raise ValueError("Expected ordinary frame IDs")
        return tuple(
            _text(item, identifier=True) for item in cast(Sequence[object], value)
        )

    @field_validator("interpretations", mode="before")
    @classmethod
    def interpretation_snapshot(
        cls, value: object
    ) -> tuple[VisionInterpretResponse, ...]:
        if (
            type(value) not in (list, tuple)
            or not 1 <= len(cast(Sequence[object], value)) <= 3
        ):
            raise ValueError("Expected bounded interpretations")
        return tuple(
            _validated(item, VisionInterpretResponse)
            if isinstance(item, BaseModel)
            else VisionInterpretResponse.model_validate(item)
            for item in cast(Sequence[object], value)
        )

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (
            not self.advisory_only
            or self.iterations_used != len(self.interpretations)
            or len(self.frame_ids) != self.iterations_used
            or len(set(self.frame_ids)) != len(self.frame_ids)
        ):
            raise ValueError("Invalid advisory loop result shape")
        for iteration, (frame_id, response) in enumerate(
            zip(self.frame_ids, self.interpretations, strict=True), 1
        ):
            if (
                response.operation_id,
                response.provider_id,
                response.model_id,
                response.frame_id,
            ) != (
                _operation_id(self.loop_id, iteration),
                self.provider_id,
                self.model_id,
                frame_id,
            ) or (iteration < self.iterations_used and response.sufficient):
                raise ValueError("Interpretation order/correlation disagrees")
        if (
            self.sufficient != self.interpretations[-1].sufficient
            or self.exhausted == self.sufficient
        ):
            raise ValueError("Invalid sufficient/exhausted evidence")
        return self


@dataclass(slots=True)
class _ActiveLoop:
    loop_id: str
    task: asyncio.Task[Any] | None
    operation_id: str | None = None
    cancelled: bool = False


class VisionLoopService:
    def __init__(self, interpreter: VisionInterpreter) -> None:
        if not isinstance(interpreter, VisionInterpreter):
            raise VisionError(VisionErrorCode.INVALID_INPUT)
        try:
            self._provider_id = _text(interpreter.provider_id, identifier=True)
        except Exception:
            raise VisionError(VisionErrorCode.INVALID_INPUT) from None
        self._interpreter = interpreter
        self._active: _ActiveLoop | None = None
        self._event_loop: asyncio.AbstractEventLoop | None = None

    @property
    def active_loop_id(self) -> str | None:
        return self._active.loop_id if self._active is not None else None

    async def run(self, request: VisionLoopRequest) -> VisionLoopResult:
        try:
            request = _validated(request, VisionLoopRequest)
            if request.provider_id != self._provider_id:
                raise ValueError("Provider correlation disagrees")
        except (ValueError, TypeError, AttributeError):
            raise VisionError(VisionErrorCode.INVALID_INPUT) from None
        current_loop = asyncio.get_running_loop()
        if self._event_loop is not None and self._event_loop is not current_loop:
            raise VisionError(VisionErrorCode.WRONG_EVENT_LOOP)
        self._event_loop = current_loop
        if self._active is not None:
            raise VisionError(VisionErrorCode.BUSY)
        active = _ActiveLoop(request.loop_id, asyncio.current_task())
        self._active = active
        results: list[VisionInterpretResponse] = []
        try:
            for iteration in range(1, request.max_iterations + 1):
                if active.cancelled:
                    raise asyncio.CancelledError()
                frame = request.frames[iteration - 1]
                active.operation_id = _operation_id(request.loop_id, iteration)
                interpret = VisionInterpretRequest(
                    operation_id=active.operation_id,
                    model_id=request.model_id,
                    query_text=request.query_text,
                    frame=frame,
                    iteration=iteration,
                )
                response = await self._interpreter.interpret(interpret)
                if active.cancelled or (
                    active.task is not None and active.task.cancelling()
                ):
                    raise asyncio.CancelledError()
                try:
                    response = _validated(response, VisionInterpretResponse)
                    response.validate_request(interpret, self._provider_id)
                except (ValueError, TypeError, AttributeError):
                    raise VisionError(VisionErrorCode.INVALID_RESPONSE) from None
                results.append(response)
                if response.sufficient:
                    break
            return VisionLoopResult(
                loop_id=request.loop_id,
                provider_id=request.provider_id,
                model_id=request.model_id,
                frame_ids=tuple(item.frame_id for item in results),
                interpretations=tuple(results),
                iterations_used=len(results),
                sufficient=results[-1].sufficient,
                exhausted=not results[-1].sufficient,
            )
        except asyncio.CancelledError:
            raise asyncio.CancelledError() from None
        except VisionError as error:
            code = (
                error.code
                if type(error.code) is VisionErrorCode
                else VisionErrorCode.INVALID_RESPONSE
            )
            raise VisionError(code) from None
        except Exception:
            raise VisionError(VisionErrorCode.INVALID_RESPONSE) from None
        finally:
            if self._active is active:
                self._active = None

    async def cancel(self, loop_id: str) -> CancellationResult:
        try:
            loop_id = _text(loop_id, identifier=True)
        except ValueError:
            raise VisionError(VisionErrorCode.INVALID_INPUT) from None
        active = self._active
        status = CancellationStatus.NOT_FOUND
        if active is not None and active.loop_id == loop_id:
            active.cancelled = True
            status = CancellationStatus.ACCEPTED
            if (
                active.task is not None
                and active.task is not asyncio.current_task()
                and not active.task.done()
                and not active.task.cancelling()
            ):
                active.task.cancel()
            if active.operation_id is not None:
                try:
                    async with asyncio.timeout(5):
                        await self._interpreter.cancel(active.operation_id)
                except asyncio.CancelledError:
                    raise asyncio.CancelledError() from None
                except Exception:
                    raise VisionError(VisionErrorCode.CLEANUP_FAILURE) from None
        return CancellationResult(
            operation_id=loop_id, provider_id=self._provider_id, status=status
        )
