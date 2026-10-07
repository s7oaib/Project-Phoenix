"""Permission-gated public discovery and a borrowed neutral player boundary.

Third-party text is inert. No frontend, browser, OAuth, persistence or worker.
API-key copies in Python/HTTPX cannot be guaranteed erased. Caller cancellation
does not undo already-entered external effects.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from contextlib import aclosing
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, ClassVar, Self, TypeVar, cast

import httpx
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    field_validator,
    model_validator,
)

from phoenix.security.permissions import (
    DecisionReason,
    PermissionDecision,
    PermissionEngine,
    PermissionGrant,
    PermissionPolicy,
    PermissionRequest,
    PermissionStatus,
    UserPermissionDecision,
)
from phoenix.security.secrets import SecretMaterial, SecretReference, SecretStore
from phoenix.tools.registry import (
    ToolDefinition,
    ToolRegistry,
    ToolResultStatus,
    ToolRiskLevel,
)

__all__ = (
    "YouTubeOperation",
    "YouTubeSearchType",
    "YouTubePlayerState",
    "YouTubeErrorCode",
    "YouTubeError",
    "YouTubeVideo",
    "YouTubeCall",
    "YouTubeIntent",
    "YouTubePermissionScope",
    "YouTubeSearchItem",
    "YouTubePlayerResult",
    "YouTubePlayerAdapter",
    "YouTubeResponse",
    "YouTubeToolResult",
    "YouTubeTool",
    "youtube_tool_definitions",
)

_SEARCH = "https://www.googleapis.com/youtube/v3/search"
_KEY = SecretReference(secret_id="youtube.api_key")
_SECONDS = 30.0
_MAX_BYTES = 256 * 1024
_POSITION = 604800
_IdPattern = r"[A-Za-z0-9_-]{1,64}"
_Seconds = Annotated[int, Field(strict=True, ge=0, le=_POSITION)]


class YouTubeOperation(StrEnum):
    SEARCH = "search"
    PLAYER_STATE = "player_state"
    QUEUE_STATE = "queue_state"
    CUE = "cue"
    PLAY = "play"
    PAUSE = "pause"
    SEEK = "seek"
    ENQUEUE = "enqueue"
    REMOVE = "remove"
    CLEAR_QUEUE = "clear_queue"


class YouTubeSearchType(StrEnum):
    VIDEO = "video"
    CHANNEL = "channel"
    PLAYLIST = "playlist"


class YouTubePlayerState(StrEnum):
    UNSTARTED = "unstarted"
    ENDED = "ended"
    PLAYING = "playing"
    PAUSED = "paused"
    BUFFERING = "buffering"
    CUED = "cued"
    UNAVAILABLE = "unavailable"


class YouTubeErrorCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    INVALID_RESPONSE = "invalid_response"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    QUOTA_EXCEEDED = "quota_exceeded"
    RATE_LIMITED = "rate_limited"
    NOT_FOUND = "not_found"
    EMBEDDING_RESTRICTED = "embedding_restricted"
    PLAYER_UNAVAILABLE = "player_unavailable"
    PLAYER_NOT_READY = "player_not_ready"
    QUEUE_FULL = "queue_full"
    QUEUE_EMPTY = "queue_empty"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    BUSY = "busy"
    CLOSED = "closed"
    WRONG_EVENT_LOOP = "wrong_event_loop"
    SECRET_UNAVAILABLE = "secret_unavailable"
    CLEANUP_FAILURE = "cleanup_failure"


_READS = (
    YouTubeOperation.SEARCH,
    YouTubeOperation.PLAYER_STATE,
    YouTubeOperation.QUEUE_STATE,
)
_PLAYER = (
    YouTubeOperation.PLAYER_STATE,
    YouTubeOperation.CUE,
    YouTubeOperation.PLAY,
    YouTubeOperation.PAUSE,
    YouTubeOperation.SEEK,
)
_PLAYER_ERRORS = (
    YouTubeErrorCode.NOT_FOUND,
    YouTubeErrorCode.EMBEDDING_RESTRICTED,
    YouTubeErrorCode.PLAYER_UNAVAILABLE,
    YouTubeErrorCode.PLAYER_NOT_READY,
    YouTubeErrorCode.UNAVAILABLE,
    YouTubeErrorCode.TIMEOUT,
)


class YouTubeError(Exception):
    """Closed category only; no collaborator diagnostics or input text."""

    def __init__(self, code: YouTubeErrorCode) -> None:
        if not any(code is member for member in YouTubeErrorCode):
            raise ValueError("invalid_input")
        self.code = code
        super().__init__(code.value)


def _text(value: object, bound: int, *, empty: bool = False) -> str:
    if (
        type(value) is not str
        or len(value) > bound
        or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value)
    ):
        raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)
    value.encode("utf-8")
    if not empty and not value.strip():
        raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)
    return value


def _id(value: object) -> str:
    return _text(value, 256).strip()


def _opaque(value: object, bound: int = 64) -> str:
    result = _text(value, bound)
    if re.fullmatch(r"[A-Za-z0-9_-]{1," + str(bound) + "}", result) is None:
        raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)
    return result


def _video_ref(value: object) -> str:
    result = _text(value, 256)
    if re.fullmatch(_IdPattern, result):
        return result
    # Full lexical forms intentionally reject encoded/repeated query parameters,
    # fragments, ports, userinfo, alternate casing and URL normalization tricks.
    for pattern in (
        r"https://(?:www\.)?youtube\.com/watch\?v=(" + _IdPattern + ")",
        r"https://youtu\.be/(" + _IdPattern + ")",
        r"https://www\.youtube\.com/embed/(" + _IdPattern + ")",
    ):
        match = re.fullmatch(pattern, result)
        if match:
            return match[1]
    raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)


def _watch(video_id: str) -> str:
    return "https://www.youtube.com/watch?v=" + video_id


def _enum(value: object, vocabulary: type[StrEnum]) -> object:
    if type(value) is not str and not any(value is member for member in vocabulary):
        raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)
    return value


M = TypeVar("M", bound=BaseModel)


def _stored(value: BaseModel, cls: type[BaseModel]) -> dict[str, object]:
    if type(value) is not cls or set(vars(value)) != set(cls.model_fields):
        raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)
    extras = value.__pydantic_extra__
    if extras is not None and (type(extras) is not dict or extras):
        raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)
    return dict(vars(value))


def _external(value: object, cls: type[M]) -> M:
    data = _stored(value, cls) if isinstance(value, BaseModel) else value
    if type(data) is not dict:
        raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)
    ordinary = cast(dict[str, object], data).copy()
    for name, vocabulary in (
        ("risk_level", ToolRiskLevel),
        ("status", PermissionStatus),
        ("reason_code", DecisionReason),
    ):
        if name in ordinary:
            _enum(ordinary[name], vocabulary)
    return cls.model_validate(ordinary)


class _Contract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
        validate_default=True,
        hide_input_in_errors=True,
    )
    _code: ClassVar[YouTubeErrorCode] = YouTubeErrorCode.INVALID_INPUT

    @model_validator(mode="wrap")
    @classmethod
    def exact(cls, value: object, handler: ModelWrapValidatorHandler[Self]) -> Self:
        try:
            return handler(
                _stored(value, cls) if isinstance(value, BaseModel) else value
            )
        except Exception:
            raise YouTubeError(cls._code) from None

    @field_validator(
        "call_id",
        "request_id",
        "actor_id",
        "operation_id",
        "player_id",
        mode="before",
        check_fields=False,
    )
    @classmethod
    def identity(cls, value: object) -> str:
        return _id(value)

    @field_validator("operation", mode="before", check_fields=False)
    @classmethod
    def operation_value(cls, value: object) -> object:
        return _enum(value, YouTubeOperation)

    @field_validator("error_code", mode="before", check_fields=False)
    @classmethod
    def error_value(cls, value: object) -> object:
        return None if value is None else _enum(value, YouTubeErrorCode)


class YouTubeVideo(_Contract):
    _code = YouTubeErrorCode.INVALID_RESPONSE
    video_id: str
    watch_url: str

    @field_validator("video_id", mode="before")
    @classmethod
    def identifier(cls, value: object) -> str:
        return _opaque(value)

    @model_validator(mode="after")
    def correlate(self) -> Self:
        if self.watch_url != _watch(self.video_id):
            raise YouTubeError(self._code)
        return self


class YouTubeCall(_Contract):
    call_id: str
    operation: YouTubeOperation
    query: str | None = Field(default=None, repr=False)
    types: tuple[YouTubeSearchType, ...] = (YouTubeSearchType.VIDEO,)
    max_results: Annotated[int, Field(strict=True, ge=1, le=10)] = 5
    video_ref: str | None = None
    start_seconds: _Seconds | None = None
    seek_seconds: _Seconds | None = None
    queue_index: Annotated[int, Field(strict=True, ge=0, le=49)] | None = None
    expected_queue_revision: Annotated[int, Field(strict=True, ge=0)] | None = None

    @field_validator("query", mode="before")
    @classmethod
    def query_value(cls, value: object) -> str | None:
        return None if value is None else _text(value, 512).strip()

    @field_validator("types", mode="before")
    @classmethod
    def types_value(cls, value: object) -> tuple[YouTubeSearchType, ...]:
        if (
            type(value) not in (tuple, list)
            or not 1 <= len(cast(Sequence[object], value)) <= 3
        ):
            raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)
        result = tuple(
            YouTubeSearchType(cast(str, _enum(x, YouTubeSearchType)))
            for x in cast(Sequence[object], value)
        )
        if len(set(result)) != len(result):
            raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)
        return result

    @field_validator("video_ref", mode="before")
    @classmethod
    def video_value(cls, value: object) -> str | None:
        return None if value is None else _video_ref(value)

    @model_validator(mode="after")
    def shape(self) -> Self:
        op = self.operation
        if (op is YouTubeOperation.SEARCH) != (self.query is not None):
            raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)
        if op is not YouTubeOperation.SEARCH and (
            self.types != (YouTubeSearchType.VIDEO,) or self.max_results != 5
        ):
            raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)
        if self.video_ref is not None and op not in (
            YouTubeOperation.CUE,
            YouTubeOperation.PLAY,
            YouTubeOperation.ENQUEUE,
        ):
            raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)
        if (
            op in (YouTubeOperation.CUE, YouTubeOperation.ENQUEUE)
            and self.video_ref is None
        ):
            raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)
        if self.start_seconds is not None and (
            op not in (YouTubeOperation.CUE, YouTubeOperation.PLAY)
            or self.video_ref is None
        ):
            raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)
        if (op is YouTubeOperation.SEEK) != (self.seek_seconds is not None):
            raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)
        if (op is YouTubeOperation.REMOVE) != (self.queue_index is not None):
            raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)
        if (op is YouTubeOperation.REMOVE) != (
            self.expected_queue_revision is not None
        ):
            raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)
        return self


def _metadata(op: YouTubeOperation) -> tuple[str, ToolRiskLevel, str]:
    risk = ToolRiskLevel.P1 if op in _READS else ToolRiskLevel.P2
    return "youtube." + op.value, risk, risk.value + ": exact YouTube scope"


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def _resource(c: YouTubeCall, player_id: str | None = None) -> str:
    return _json(
        [
            "youtube-v1",
            c.operation.value,
            sha256(c.query.encode()).hexdigest() if c.query is not None else None,
            [x.value for x in c.types],
            c.max_results,
            c.video_ref,
            c.start_seconds,
            c.seek_seconds,
            c.queue_index,
            player_id,
            c.expected_queue_revision,
        ]
    )


def _scope(resource: str, operation: YouTubeOperation) -> YouTubeCall:
    data = json.loads(_text(resource, 2048))
    if (
        type(data) is not list
        or len(data) != 11
        or data[:2] != ["youtube-v1", operation.value]
    ):
        raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)
    digest = data[2]
    if operation is YouTubeOperation.SEARCH:
        if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)
    elif digest is not None:
        raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)
    call = YouTubeCall(
        call_id="scope",
        operation=operation,
        query="scope" if digest is not None else None,
        types=data[3],
        max_results=data[4],
        video_ref=data[5],
        start_seconds=data[6],
        seek_seconds=data[7],
        queue_index=data[8],
        expected_queue_revision=data[10],
    )
    player_id = data[9]
    if player_id is not None and (
        operation not in _PLAYER or _id(player_id) != player_id
    ):
        raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)
    canonical = json.loads(_resource(call, player_id))
    canonical[2] = digest
    if resource != _json(canonical):
        raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)
    return call


class YouTubeIntent(_Contract):
    call_id: str
    operation: YouTubeOperation
    tool_id: str
    action: str
    resource: str
    risk_level: ToolRiskLevel
    permission_requirement: str

    @field_validator("risk_level", mode="before")
    @classmethod
    def risk(cls, value: object) -> object:
        return _enum(value, ToolRiskLevel)

    @model_validator(mode="after")
    def canonical(self) -> Self:
        tool, risk, requirement = _metadata(self.operation)
        if (
            self.tool_id,
            self.action,
            self.risk_level,
            self.permission_requirement,
        ) != (tool, self.operation.value, risk, requirement):
            raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)
        try:
            _scope(self.resource, self.operation)
        except Exception:
            raise YouTubeError(YouTubeErrorCode.INVALID_INPUT) from None
        return self


class YouTubePermissionScope(_Contract):
    request_id: str
    actor_id: str
    tool_id: str
    action: str
    resource: str


def _url(kind: YouTubeSearchType, identity: str) -> str:
    if kind is YouTubeSearchType.VIDEO:
        return _watch(identity)
    if kind is YouTubeSearchType.CHANNEL:
        return "https://www.youtube.com/channel/" + identity
    return "https://www.youtube.com/playlist?list=" + identity


class YouTubeSearchItem(_Contract):
    _code = YouTubeErrorCode.INVALID_RESPONSE
    item_type: YouTubeSearchType
    resource_id: str
    title: str = Field(repr=False)
    channel_id: str | None = None
    channel_title: str | None = Field(default=None, repr=False)
    published_at: AwareDatetime | None = None
    canonical_url: str

    @field_validator("item_type", mode="before")
    @classmethod
    def kind(cls, value: object) -> object:
        return _enum(value, YouTubeSearchType)

    @field_validator("resource_id", "channel_id", mode="before")
    @classmethod
    def identifiers(cls, value: object) -> str | None:
        return None if value is None else _opaque(value, 128)

    @field_validator("title", "channel_title", mode="before")
    @classmethod
    def names(cls, value: object) -> str | None:
        return None if value is None else _text(value, 512, empty=True)

    @field_validator("published_at", mode="before")
    @classmethod
    def timestamp(cls, value: object) -> object:
        if value is not None and type(value) not in (str, datetime):
            raise YouTubeError(cls._code)
        if type(value) is str:
            _text(value, 64)
        return value

    @field_validator("published_at")
    @classmethod
    def utc(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None

    @model_validator(mode="after")
    def correlate(self) -> Self:
        if self.item_type is YouTubeSearchType.VIDEO:
            _opaque(self.resource_id)
        if self.canonical_url != _url(self.item_type, self.resource_id):
            raise YouTubeError(self._code)
        return self


class YouTubePlayerResult(_Contract):
    """Adapter evidence; acceptance never proves rendering or physical playback."""

    _code = YouTubeErrorCode.INVALID_RESPONSE
    operation_id: str
    player_id: str
    operation: YouTubeOperation
    requested_video_id: str | None = None
    requested_seconds: _Seconds | None = None
    video_id: str | None = None
    state: YouTubePlayerState
    position_seconds: float | None = None
    command_accepted: bool = Field(default=False, strict=True)
    error_code: YouTubeErrorCode | None = None

    @field_validator("requested_video_id", "video_id", mode="before")
    @classmethod
    def videos(cls, value: object) -> str | None:
        return None if value is None else _opaque(value)

    @field_validator("state", mode="before")
    @classmethod
    def state_value(cls, value: object) -> object:
        return _enum(value, YouTubePlayerState)

    @field_validator("position_seconds", mode="before")
    @classmethod
    def position(cls, value: object) -> float | None:
        if value is None:
            return None
        if type(value) not in (int, float):
            raise YouTubeError(cls._code)
        result = float(cast(int | float, value))
        if not math.isfinite(result) or not 0 <= result <= _POSITION:
            raise YouTubeError(cls._code)
        return result

    @model_validator(mode="after")
    def shape(self) -> Self:
        if self.operation not in _PLAYER:
            raise YouTubeError(self._code)
        # Reuse call shape admission without permitting any new adapter command.
        try:
            YouTubeCall(
                call_id=self.operation_id,
                operation=self.operation,
                video_ref=self.requested_video_id,
                start_seconds=self.requested_seconds
                if self.operation in (YouTubeOperation.CUE, YouTubeOperation.PLAY)
                else None,
                seek_seconds=self.requested_seconds
                if self.operation is YouTubeOperation.SEEK
                else None,
            )
        except Exception:
            raise YouTubeError(self._code) from None
        if (
            self.operation in (YouTubeOperation.PLAYER_STATE, YouTubeOperation.PAUSE)
            and self.requested_seconds is not None
        ):
            raise YouTubeError(self._code)
        if self.error_code is not None:
            if self.error_code not in _PLAYER_ERRORS or self.command_accepted:
                raise YouTubeError(self._code)
        elif self.operation is YouTubeOperation.PLAYER_STATE:
            if self.command_accepted:
                raise YouTubeError(self._code)
        elif (
            not self.command_accepted
            or self.video_id is None
            or self.state is YouTubePlayerState.UNAVAILABLE
        ):
            raise YouTubeError(self._code)
        if (
            self.error_code is None
            and self.requested_video_id is not None
            and self.video_id != self.requested_video_id
        ):
            raise YouTubeError(self._code)
        if self.video_id is None and self.position_seconds is not None:
            raise YouTubeError(self._code)
        return self

    @property
    def watch_url(self) -> str | None:
        accepted = YouTubePlayerResult.model_validate(self)
        return None if accepted.video_id is None else _watch(accepted.video_id)


class YouTubePlayerAdapter(ABC):
    """Borrowed interface only; implementations own their external player surface."""

    def __init__(self, player_id: str) -> None:
        if hasattr(self, "_player_id"):
            raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)
        try:
            self._player_id = _id(player_id)
        except Exception:
            raise YouTubeError(YouTubeErrorCode.INVALID_INPUT) from None

    @property
    def player_id(self) -> str:
        return self._player_id

    @abstractmethod
    async def observe(self, operation_id: str) -> YouTubePlayerResult: ...

    @abstractmethod
    async def cue(
        self, operation_id: str, video_id: str, start_seconds: int | None
    ) -> YouTubePlayerResult: ...

    @abstractmethod
    async def play(
        self, operation_id: str, video_id: str | None, start_seconds: int | None
    ) -> YouTubePlayerResult: ...

    @abstractmethod
    async def pause(self, operation_id: str) -> YouTubePlayerResult: ...

    @abstractmethod
    async def seek(self, operation_id: str, seconds: int) -> YouTubePlayerResult: ...


class YouTubeResponse(_Contract):
    _code = YouTubeErrorCode.INVALID_RESPONSE
    operation: YouTubeOperation
    items: tuple[YouTubeSearchItem, ...] = ()
    player: YouTubePlayerResult | None = None
    queue: tuple[YouTubeVideo, ...] | None = None
    queue_revision: Annotated[int, Field(strict=True, ge=0)] | None = None

    @field_validator("items", mode="before")
    @classmethod
    def item_snapshots(cls, value: object) -> tuple[YouTubeSearchItem, ...]:
        if type(value) not in (tuple, list) or len(cast(Sequence[object], value)) > 10:
            raise YouTubeError(cls._code)
        return tuple(
            YouTubeSearchItem.model_validate(x) for x in cast(Sequence[object], value)
        )

    @field_validator("queue", mode="before")
    @classmethod
    def queue_snapshots(cls, value: object) -> tuple[YouTubeVideo, ...] | None:
        if value is None:
            return None
        if type(value) not in (tuple, list) or len(cast(Sequence[object], value)) > 50:
            raise YouTubeError(cls._code)
        return tuple(
            YouTubeVideo.model_validate(x) for x in cast(Sequence[object], value)
        )

    @field_validator("player", mode="before")
    @classmethod
    def player_snapshot(cls, value: object) -> YouTubePlayerResult | None:
        return None if value is None else YouTubePlayerResult.model_validate(value)

    @model_validator(mode="after")
    def shape(self) -> Self:
        if self.operation is YouTubeOperation.SEARCH:
            valid = self.player is None and self.queue is None
        elif self.operation in _PLAYER:
            valid = (
                not self.items
                and self.queue is None
                and self.player is not None
                and self.player.operation is self.operation
                and self.player.error_code is None
            )
        else:
            valid = not self.items and self.player is None and self.queue is not None
        if (self.queue is None) != (self.queue_revision is None):
            valid = False
        if not valid:
            raise YouTubeError(self._code)
        return self


def _status(code: YouTubeErrorCode) -> ToolResultStatus:
    if code is YouTubeErrorCode.TIMEOUT:
        return ToolResultStatus.TIMEOUT
    if code is YouTubeErrorCode.CANCELLED:
        return ToolResultStatus.CANCELLED
    if code in (YouTubeErrorCode.UNAVAILABLE, YouTubeErrorCode.PLAYER_UNAVAILABLE):
        return ToolResultStatus.UNAVAILABLE_DEPENDENCY
    return ToolResultStatus.FAILURE


class YouTubeToolResult(_Contract):
    _code = YouTubeErrorCode.INVALID_RESPONSE
    intent: YouTubeIntent
    permission_scope: YouTubePermissionScope
    permission_decision: PermissionDecision
    attempted: bool = Field(strict=True)
    status: ToolResultStatus
    response: YouTubeResponse | None = None
    error_code: YouTubeErrorCode | None = None

    @field_validator("intent", mode="before")
    @classmethod
    def intent_snapshot(cls, value: object) -> YouTubeIntent:
        return YouTubeIntent.model_validate(value)

    @field_validator("permission_scope", mode="before")
    @classmethod
    def scope_snapshot(cls, value: object) -> YouTubePermissionScope:
        return YouTubePermissionScope.model_validate(value)

    @field_validator("permission_decision", mode="before")
    @classmethod
    def decision_snapshot(cls, value: object) -> PermissionDecision:
        return _external(value, PermissionDecision)

    @field_validator("response", mode="before")
    @classmethod
    def response_snapshot(cls, value: object) -> YouTubeResponse | None:
        return None if value is None else YouTubeResponse.model_validate(value)

    @field_validator("status", mode="before")
    @classmethod
    def status_value(cls, value: object) -> object:
        return _enum(value, ToolResultStatus)

    @model_validator(mode="after")
    def correlate(self) -> Self:
        i, p, d = self.intent, self.permission_scope, self.permission_decision
        if (p.request_id, p.tool_id, p.action, p.resource) != (
            i.call_id,
            i.tool_id,
            i.action,
            i.resource,
        ) or (
            d.request_id,
            d.actor_id,
            d.tool_id,
            d.risk_level,
            d.permission_requirement,
        ) != (
            p.request_id,
            p.actor_id,
            i.tool_id,
            i.risk_level,
            i.permission_requirement,
        ):
            raise YouTubeError(self._code)
        if d.status is not PermissionStatus.ALLOWED:
            if (
                self.attempted
                or self.status is not ToolResultStatus.DENIED
                or self.response is not None
                or self.error_code is not None
            ):
                raise YouTubeError(self._code)
        elif not self.attempted:
            raise YouTubeError(self._code)
        elif self.status is ToolResultStatus.SUCCESS:
            r = self.response
            if (
                r is None
                or self.error_code is not None
                or r.operation is not i.operation
            ):
                raise YouTubeError(self._code)
            bound = _scope(i.resource, i.operation)
            if i.operation is YouTubeOperation.SEARCH:
                if len(r.items) > bound.max_results or any(
                    x.item_type not in bound.types for x in r.items
                ):
                    raise YouTubeError(self._code)
            if r.player is not None:
                player = r.player
                seconds = (
                    bound.seek_seconds
                    if bound.operation is YouTubeOperation.SEEK
                    else bound.start_seconds
                )
                if (
                    player.operation_id,
                    player.player_id,
                    player.requested_video_id,
                    player.requested_seconds,
                ) != (i.call_id, json.loads(i.resource)[9], bound.video_ref, seconds):
                    raise YouTubeError(self._code)
            if i.operation is YouTubeOperation.ENQUEUE and (
                not r.queue
                or r.queue[-1].video_id != bound.video_ref
                or r.queue_revision is None
                or r.queue_revision < 1
            ):
                raise YouTubeError(self._code)
            if i.operation is YouTubeOperation.CLEAR_QUEUE and r.queue != ():
                raise YouTubeError(self._code)
            if i.operation is YouTubeOperation.REMOVE and (
                bound.expected_queue_revision is None
                or r.queue_revision != bound.expected_queue_revision + 1
                or r.queue is None
                or len(r.queue) >= 50
            ):
                raise YouTubeError(self._code)
        elif (
            self.response is not None
            or self.error_code is None
            or self.status is not _status(self.error_code)
        ):
            raise YouTubeError(self._code)
        return self


def youtube_tool_definitions() -> tuple[ToolDefinition, ...]:
    input_schema = YouTubeCall.model_json_schema()
    output_schema = YouTubeToolResult.model_json_schema()
    return tuple(
        ToolDefinition(
            tool_id=tool,
            description="YouTube " + op.value + "; third-party evidence is untrusted.",
            input_schema=input_schema,
            output_schema=output_schema,
            risk_level=risk,
            permission_requirement=requirement,
            timeout_seconds=_SECONDS,
            cancellation_supported=True,
            audit_behavior="no mandatory durable audit",
            supported_platforms=("win32", "linux", "darwin"),
            supported_environments=("local",),
        )
        for op in YouTubeOperation
        for tool, risk, requirement in (_metadata(op),)
    )


def _object(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise YouTubeError(YouTubeErrorCode.INVALID_RESPONSE)
    return cast(dict[str, object], value)


def _load_json(data: bytes) -> object:
    def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
        if len({key for key, _ in values}) != len(values):
            raise YouTubeError(YouTubeErrorCode.INVALID_RESPONSE)
        return dict(values)

    value: object = json.loads(data, object_pairs_hook=pairs)
    stack = [(value, 0)]
    count = 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if depth > 32 or count > 10000:
            raise YouTubeError(YouTubeErrorCode.INVALID_RESPONSE)
        if type(item) is dict:
            stack.extend(
                (entry, depth + 1)
                for pair in cast(dict[str, object], item).items()
                for entry in pair
            )
        elif type(item) is list:
            stack.extend((entry, depth + 1) for entry in cast(list[object], item))
        elif type(item) is str:
            if len(item) > 4096:
                raise YouTubeError(YouTubeErrorCode.INVALID_RESPONSE)
            item.encode("utf-8")
        elif type(item) is float and not math.isfinite(item):
            raise YouTubeError(YouTubeErrorCode.INVALID_RESPONSE)
    return value


def _search_response(c: YouTubeCall, value: object) -> YouTubeResponse:
    data = _object(value)
    entries = data.get("items")
    if type(entries) is not list or len(entries) > c.max_results:
        raise YouTubeError(YouTubeErrorCode.INVALID_RESPONSE)
    normalized = []
    for entry in entries:
        raw = _object(entry)
        identity = _object(raw.get("id"))
        snippet = _object(raw.get("snippet"))
        kind = identity.get("kind")
        selected = next((x for x in c.types if kind == "youtube#" + x.value), None)
        if selected is None:
            raise YouTubeError(YouTubeErrorCode.INVALID_RESPONSE)
        # Reject contradictory ID evidence rather than guessing which identity wins.
        if any(
            key in identity
            for key in ("videoId", "channelId", "playlistId")
            if key != selected.value + "Id"
        ):
            raise YouTubeError(YouTubeErrorCode.INVALID_RESPONSE)
        resource_id = _opaque(
            identity.get(selected.value + "Id"),
            64 if selected is YouTubeSearchType.VIDEO else 128,
        )
        normalized.append(
            YouTubeSearchItem.model_validate(
                dict(
                    item_type=selected,
                    resource_id=resource_id,
                    title=snippet.get("title"),
                    channel_id=snippet.get("channelId"),
                    channel_title=snippet.get("channelTitle"),
                    published_at=snippet.get("publishedAt"),
                    canonical_url=_url(selected, resource_id),
                )
            )
        )
    return YouTubeResponse(operation=c.operation, items=tuple(normalized))


class YouTubeTool:
    """One explicit caller operation; collaborators and consent provenance borrowed."""

    def __init__(
        self,
        registry: ToolRegistry,
        policy: PermissionPolicy,
        store: SecretStore,
        player: YouTubePlayerAdapter | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        try:
            if (
                hasattr(self, "_engine")
                or type(registry) is not ToolRegistry
                or type(policy) is not PermissionPolicy
            ):
                raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)
            if client is not None and (
                type(client) is not httpx.AsyncClient or transport is not None
            ):
                raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)
            if transport is not None and not isinstance(
                transport, httpx.AsyncBaseTransport
            ):
                raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)
            if not all(
                callable(getattr(store, x, None)) for x in ("resolve", "put", "delete")
            ):
                raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)
            if player is not None and not isinstance(player, YouTubePlayerAdapter):
                raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)
            player_id = _id(player.player_id) if player is not None else None
            snapshot = ToolRegistry(registry.list_tools())
            for definition in youtube_tool_definitions():
                if snapshot.require(definition.tool_id) != definition:
                    raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)
            engine = PermissionEngine(snapshot, _external(policy, PermissionPolicy))
        except Exception:
            raise YouTubeError(YouTubeErrorCode.INVALID_INPUT) from None
        self._registry, self._engine, self._store = snapshot, engine, store
        self._player, self._player_id = player, player_id
        self._client, self._transport, self._owned = client, transport, client is None
        self._closed = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._active: str | None = None
        self._expires: float | None = None
        self._queue: tuple[YouTubeVideo, ...] = ()
        self._queue_revision = 0

    @property
    def active_operation_ids(self) -> tuple[str, ...]:
        return () if self._active is None else (self._active,)

    @property
    def closed(self) -> bool:
        return self._closed

    def _bind(self, *, closing: bool = False) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise YouTubeError(YouTubeErrorCode.WRONG_EVENT_LOOP)
        if self._closed and not closing:
            raise YouTubeError(YouTubeErrorCode.CLOSED)

    def _guard(self) -> None:
        task = asyncio.current_task()
        if self._closed or (task is not None and task.cancelling()):
            raise asyncio.CancelledError
        if (
            self._expires is not None
            and self._loop is not None
            and self._loop.time() >= self._expires
        ):
            raise YouTubeError(YouTubeErrorCode.TIMEOUT)

    def describe(self, call: YouTubeCall) -> YouTubeIntent:
        accepted = YouTubeCall.model_validate(call)
        tool, _, _ = _metadata(accepted.operation)
        definition = self._registry.require(tool)
        return YouTubeIntent(
            call_id=accepted.call_id,
            operation=accepted.operation,
            tool_id=tool,
            action=accepted.operation.value,
            resource=_resource(
                accepted, self._player_id if accepted.operation in _PLAYER else None
            ),
            risk_level=definition.risk_level,
            permission_requirement=definition.permission_requirement,
        )

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                verify=True,
                trust_env=False,
                follow_redirects=False,
                timeout=httpx.Timeout(10, connect=5, pool=5),
                transport=self._transport,
            )
        return self._client

    def _key(self) -> str:
        try:
            material = self._store.resolve(_KEY)
            if type(material) is not SecretMaterial:
                raise YouTubeError(YouTubeErrorCode.SECRET_UNAVAILABLE)
            with material:
                key = _text(material.reveal_text_for_trusted_use(), 2560)
                if re.fullmatch(r"[A-Za-z0-9_-]{1,2560}", key) is None:
                    raise YouTubeError(YouTubeErrorCode.SECRET_UNAVAILABLE)
            self._guard()
            return key
        except Exception:
            self._guard()
            raise YouTubeError(YouTubeErrorCode.SECRET_UNAVAILABLE) from None

    async def _search(self, c: YouTubeCall) -> YouTubeResponse:
        key = self._key()
        try:
            self._guard()
            if key in cast(str, c.query):
                raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)
            # Direct Request avoids borrowed-client default params/cookies/headers.
            wire = httpx.Request(
                "GET",
                _SEARCH,
                params={
                    "part": "snippet",
                    "q": cast(str, c.query),
                    "type": ",".join(x.value for x in c.types),
                    "maxResults": str(c.max_results),
                },
                headers={"X-Goog-Api-Key": key},
                extensions={"timeout": httpx.Timeout(10, connect=5, pool=5).as_dict()},
            )
            async with aclosing(
                await self._http().send(
                    wire, stream=True, auth=None, follow_redirects=False
                )
            ) as response:
                self._guard()
                status = response.status_code
                if status not in (200, 403):
                    raise YouTubeError(
                        YouTubeErrorCode.UNAUTHORIZED
                        if status == 401
                        else YouTubeErrorCode.NOT_FOUND
                        if status == 404
                        else YouTubeErrorCode.RATE_LIMITED
                        if status == 429
                        else YouTubeErrorCode.UNAVAILABLE
                        if status >= 500
                        else YouTubeErrorCode.INVALID_RESPONSE
                    )
                content = bytearray()
                limit = _MAX_BYTES if status == 200 else 8192
                async for chunk in response.aiter_bytes():
                    self._guard()
                    if len(content) + len(chunk) > limit:
                        raise YouTubeError(
                            YouTubeErrorCode.FORBIDDEN
                            if status == 403
                            else YouTubeErrorCode.INVALID_RESPONSE
                        )
                    content.extend(chunk)
                self._guard()
                if status == 403:
                    quota = False
                    try:
                        error = _object(
                            _object(_load_json(bytes(content))).get("error")
                        )
                        reasons = error.get("errors")
                        if type(reasons) is list and len(reasons) <= 16:
                            quota = any(
                                _object(x).get("reason") == "quotaExceeded"
                                for x in reasons
                            )
                    except Exception:
                        pass  # Malformed failure bodies cannot promote diagnostics.
                    finally:
                        content.clear()
                    raise YouTubeError(
                        YouTubeErrorCode.QUOTA_EXCEEDED
                        if quota
                        else YouTubeErrorCode.FORBIDDEN
                    )
                value = _load_json(bytes(content))
                content.clear()
                try:
                    result = _search_response(c, value)
                except Exception:
                    raise YouTubeError(YouTubeErrorCode.INVALID_RESPONSE) from None
                # Do not let reflected credentials enter normalized public evidence.
                if key in result.model_dump_json():
                    raise YouTubeError(YouTubeErrorCode.INVALID_RESPONSE)
                return result
        except httpx.TimeoutException:
            self._guard()
            raise YouTubeError(YouTubeErrorCode.TIMEOUT) from None
        except httpx.HTTPError:
            self._guard()
            raise YouTubeError(YouTubeErrorCode.UNAVAILABLE) from None
        except YouTubeError:
            self._guard()
            raise
        except Exception:
            self._guard()
            raise YouTubeError(YouTubeErrorCode.INVALID_RESPONSE) from None

    async def _player_call(self, c: YouTubeCall) -> YouTubeResponse:
        player = self._player
        if player is None:
            raise YouTubeError(YouTubeErrorCode.PLAYER_UNAVAILABLE)
        try:
            if player.player_id != self._player_id:
                raise YouTubeError(YouTubeErrorCode.INVALID_RESPONSE)
            self._guard()
            if c.operation is YouTubeOperation.PLAYER_STATE:
                value = await player.observe(c.call_id)
            elif c.operation is YouTubeOperation.CUE:
                value = await player.cue(
                    c.call_id, cast(str, c.video_ref), c.start_seconds
                )
            elif c.operation is YouTubeOperation.PLAY:
                value = await player.play(c.call_id, c.video_ref, c.start_seconds)
            elif c.operation is YouTubeOperation.PAUSE:
                value = await player.pause(c.call_id)
            else:
                value = await player.seek(c.call_id, cast(int, c.seek_seconds))
            self._guard()
            result = YouTubePlayerResult.model_validate(value)
            seconds = (
                c.seek_seconds
                if c.operation is YouTubeOperation.SEEK
                else c.start_seconds
            )
            if (
                result.operation_id,
                result.player_id,
                result.operation,
                result.requested_video_id,
                result.requested_seconds,
            ) != (
                c.call_id,
                self._player_id,
                c.operation,
                c.video_ref,
                seconds,
            ) or player.player_id != self._player_id:
                raise YouTubeError(YouTubeErrorCode.INVALID_RESPONSE)
            if result.error_code is not None:
                raise YouTubeError(result.error_code)
            return YouTubeResponse(operation=c.operation, player=result)
        except YouTubeError as error:
            self._guard()
            code = (
                error.code
                if type(error) is YouTubeError
                and error.code in (*_PLAYER_ERRORS, YouTubeErrorCode.INVALID_RESPONSE)
                else YouTubeErrorCode.INVALID_RESPONSE
            )
            raise YouTubeError(code) from None
        except Exception:
            self._guard()
            raise YouTubeError(YouTubeErrorCode.PLAYER_UNAVAILABLE) from None

    def _queue_call(self, c: YouTubeCall) -> YouTubeResponse:
        self._guard()
        if c.operation is YouTubeOperation.QUEUE_STATE:
            return YouTubeResponse(
                operation=c.operation,
                queue=self._queue,
                queue_revision=self._queue_revision,
            )
        if c.operation is YouTubeOperation.ENQUEUE:
            if len(self._queue) >= 50:
                raise YouTubeError(YouTubeErrorCode.QUEUE_FULL)
            video_id = cast(str, c.video_ref)
            proposed = (
                *self._queue,
                YouTubeVideo(video_id=video_id, watch_url=_watch(video_id)),
            )
        elif c.operation is YouTubeOperation.CLEAR_QUEUE:
            proposed = ()
        else:
            if (
                c.expected_queue_revision != self._queue_revision
                or c.queue_index is None
                or c.queue_index >= len(self._queue)
            ):
                raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)
            proposed = self._queue[: c.queue_index] + self._queue[c.queue_index + 1 :]
        revision = self._queue_revision + int(proposed != self._queue)
        result = YouTubeResponse(
            operation=c.operation, queue=proposed, queue_revision=revision
        )
        self._guard()
        self._queue, self._queue_revision = proposed, revision
        return result

    async def execute(
        self,
        call: YouTubeCall,
        request: PermissionRequest,
        *,
        grants: Sequence[PermissionGrant] = (),
        user_decision: UserPermissionDecision | None = None,
    ) -> YouTubeToolResult:
        try:
            accepted = YouTubeCall.model_validate(call)
            intent = self.describe(accepted)
            req = _external(request, PermissionRequest)
            if (req.request_id, req.tool_id, req.action, req.resource) != (
                intent.call_id,
                intent.tool_id,
                intent.action,
                intent.resource,
            ):
                raise YouTubeError(YouTubeErrorCode.INVALID_INPUT)
            decision = self._engine.evaluate(
                req, grants=grants, user_decision=user_decision
            )
            scope = YouTubePermissionScope(
                request_id=req.request_id,
                actor_id=req.actor_id,
                tool_id=req.tool_id,
                action=req.action,
                resource=req.resource,
            )
        except Exception:
            raise YouTubeError(YouTubeErrorCode.INVALID_INPUT) from None
        self._bind()
        evidence = dict(
            intent=intent, permission_scope=scope, permission_decision=decision
        )
        if decision.status is not PermissionStatus.ALLOWED:
            return YouTubeToolResult.model_validate(
                evidence | dict(attempted=False, status=ToolResultStatus.DENIED)
            )
        self._guard()
        if self._active is not None:
            raise YouTubeError(YouTubeErrorCode.BUSY)
        self._active = accepted.call_id
        try:
            code = None
            response = None
            try:
                async with asyncio.timeout(_SECONDS) as deadline:
                    self._expires = deadline.when()
                    if accepted.operation is YouTubeOperation.SEARCH:
                        response = await self._search(accepted)
                    elif accepted.operation in _PLAYER:
                        response = await self._player_call(accepted)
                    else:
                        response = self._queue_call(accepted)
                    self._guard()
                    if deadline.expired():
                        raise TimeoutError
            except TimeoutError:
                code = YouTubeErrorCode.TIMEOUT
            except YouTubeError as error:
                code = (
                    error.code
                    if type(error) is YouTubeError
                    and any(error.code is x for x in YouTubeErrorCode)
                    else YouTubeErrorCode.INVALID_RESPONSE
                )
            except Exception:
                code = YouTubeErrorCode.INVALID_RESPONSE
            finally:
                self._expires = None
            self._guard()
            return YouTubeToolResult.model_validate(
                evidence
                | dict(
                    attempted=True,
                    status=ToolResultStatus.SUCCESS if code is None else _status(code),
                    response=response if code is None else None,
                    error_code=code,
                )
            )
        finally:
            self._active = None

    async def aclose(self) -> None:
        self._bind(closing=True)
        self._closed = True
        self._queue = ()
        if self._owned and self._client is not None:
            try:
                async with asyncio.timeout(_SECONDS):
                    await self._client.aclose()
            except Exception:
                raise YouTubeError(YouTubeErrorCode.CLEANUP_FAILURE) from None
        # Active caller keeps its observable ID until it actually exits.
