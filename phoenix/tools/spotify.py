"""Permission-gated Spotify control; consent and collaborator lifetimes are trusted.

Catalog/device text is inert untrusted data. No audio, cache, agent or UI runtime.
SecretMaterial wiping is best effort; Python/HTTPX can retain immutable copies.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import math
import re
import secrets
from collections.abc import Sequence
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, ClassVar, Self, TypeVar, cast
from urllib.parse import urlencode, urlsplit

import httpx
from pydantic import (
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
    "SpotifyOperation",
    "SpotifyItemType",
    "SpotifyErrorCode",
    "SpotifyError",
    "SpotifyCall",
    "SpotifyIntent",
    "SpotifyPermissionScope",
    "SpotifyItem",
    "SpotifyDevice",
    "SpotifyResponse",
    "SpotifyToolResult",
    "SpotifyAuthorization",
    "spotify_tool_definitions",
    "SpotifyTool",
    "SPOTIFY_SCOPES",
)

SPOTIFY_SCOPES = ("user-read-playback-state", "user-modify-playback-state")
_API = "https://api.spotify.com/v1"
_TOKEN = "https://accounts.spotify.com/api/token"
_AUTHORIZE = "https://accounts.spotify.com/authorize"
_ACCESS = SecretReference(secret_id="spotify.access_token")
_REFRESH = SecretReference(secret_id="spotify.refresh_token")
_SECONDS = 30.0
_MAX_BYTES = 256 * 1024


class SpotifyOperation(StrEnum):
    SEARCH = "search"
    PLAYBACK_STATE = "playback_state"
    DEVICES = "devices"
    PLAY = "play"
    PAUSE = "pause"
    NEXT = "next"
    PREVIOUS = "previous"
    TRANSFER = "transfer"
    VOLUME = "volume"


class SpotifyItemType(StrEnum):
    TRACK = "track"
    ARTIST = "artist"
    ALBUM = "album"
    PLAYLIST = "playlist"
    EPISODE = "episode"  # Read-only current playback evidence, never playable input.


class SpotifyErrorCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    INVALID_RESPONSE = "invalid_response"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    PREMIUM_OR_DEVICE_REQUIRED = "premium_or_device_required"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    BUSY = "busy"
    CLOSED = "closed"
    WRONG_EVENT_LOOP = "wrong_event_loop"
    SECRET_UNAVAILABLE = "secret_unavailable"
    OAUTH_STATE_MISMATCH = "oauth_state_mismatch"
    OAUTH_FAILURE = "oauth_failure"
    CLEANUP_FAILURE = "cleanup_failure"


class SpotifyError(Exception):
    def __init__(self, code: SpotifyErrorCode) -> None:
        if not any(code is member for member in SpotifyErrorCode):
            raise ValueError("invalid_input")
        self.code = code
        super().__init__(code.value)


def _text(value: object, bound: int, *, empty: bool = False) -> str:
    if (
        type(value) is not str
        or len(value) > bound
        or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value)
    ):
        raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
    value.encode("utf-8")
    if not empty and not value.strip():
        raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
    return value


def _id(value: object) -> str:
    result = _text(value, 256).strip()
    return result


def _device_id(value: object) -> str:
    result = _text(value, 256)
    if re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", result) is None:
        raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
    return result


def _uri(value: object, kinds: tuple[str, ...]) -> str:
    result = _text(value, 128)
    pieces = result.split(":")
    if (
        len(pieces) != 3
        or pieces[0] != "spotify"
        or pieces[1] not in kinds
        or (re.fullmatch(r"[A-Za-z0-9]{22}", pieces[2]) is None)
    ):
        raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
    return result


def _redirect(value: object) -> str:
    result = _text(value, 2048)
    if (
        any(c.isspace() for c in result)
        or "\\" in result
        or "#" in result
        or "?" in result
    ):
        raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
    parts = urlsplit(result)
    host = parts.hostname
    if not host or parts.username is not None or parts.password is not None:
        raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
    if host.lower().rstrip(".") == "localhost" or host.lower().rstrip(".").endswith(
        ".localhost"
    ):
        raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
    if parts.port is not None and not 1 <= parts.port <= 65535:
        raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
    if parts.netloc.endswith(":"):
        raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
    if parts.scheme not in ("http", "https") or "%" in host or "*" in host:
        raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", host) is None:
            raise SpotifyError(SpotifyErrorCode.INVALID_INPUT) from None
        if any(
            not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
            for label in host.split(".")
        ):
            raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
        address = None
    if parts.scheme == "http" and (
        address is None or str(address) not in ("127.0.0.1", "::1")
    ):
        raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
    return result  # Never normalize a registered redirect URI.


def _enum(value: object, vocabulary: type[StrEnum]) -> object:
    if type(value) is not str and not any(value is member for member in vocabulary):
        raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
    return value


M = TypeVar("M", bound=BaseModel)


def _stored(value: BaseModel, cls: type[BaseModel]) -> dict[str, object]:
    if type(value) is not cls or set(vars(value)) != set(cls.model_fields):
        raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
    extras = value.__pydantic_extra__
    if extras is not None and (type(extras) is not dict or extras):
        raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
    return dict(vars(value))


def _external(value: object, cls: type[M]) -> M:
    data = _stored(value, cls) if isinstance(value, BaseModel) else value
    if type(data) is not dict:
        raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
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
    _code: ClassVar[SpotifyErrorCode] = SpotifyErrorCode.INVALID_INPUT

    @model_validator(mode="wrap")
    @classmethod
    def exact(cls, value: object, handler: ModelWrapValidatorHandler[Self]) -> Self:
        try:
            return handler(
                _stored(value, cls) if isinstance(value, BaseModel) else value
            )
        except Exception:
            raise SpotifyError(cls._code) from None

    @field_validator(
        "call_id", "actor_id", "request_id", mode="before", check_fields=False
    )
    @classmethod
    def identity(cls, value: object) -> str:
        return _id(value)

    @field_validator("operation", mode="before", check_fields=False)
    @classmethod
    def operation_value(cls, value: object) -> object:
        return _enum(value, SpotifyOperation)


class SpotifyCall(_Contract):
    call_id: str
    operation: SpotifyOperation
    query: str | None = Field(default=None, repr=False)
    types: tuple[SpotifyItemType, ...] = (SpotifyItemType.TRACK,)
    limit: Annotated[int, Field(strict=True, ge=1, le=10)] = 5
    offset: Annotated[int, Field(strict=True, ge=0, le=1000)] = 0
    media_uri: str | None = None
    device_id: str | None = None
    volume_percent: Annotated[int, Field(strict=True, ge=0, le=100)] | None = None
    transfer_play: bool = Field(default=False, strict=True)

    @field_validator("query", mode="before")
    @classmethod
    def query_value(cls, value: object) -> str | None:
        return None if value is None else _text(value, 512).strip()

    @field_validator("types", mode="before")
    @classmethod
    def types_value(cls, value: object) -> tuple[SpotifyItemType, ...]:
        if (
            type(value) not in (tuple, list)
            or not 1 <= len(cast(Sequence[object], value)) <= 4
        ):
            raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
        result = tuple(
            SpotifyItemType(cast(str, _enum(item, SpotifyItemType)))
            for item in cast(Sequence[object], value)
        )
        if SpotifyItemType.EPISODE in result or len(set(result)) != len(result):
            raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
        return result

    @field_validator("media_uri", mode="before")
    @classmethod
    def media_value(cls, value: object) -> str | None:
        return (
            None
            if value is None
            else _uri(value, ("track", "album", "artist", "playlist"))
        )

    @field_validator("device_id", mode="before")
    @classmethod
    def device_value(cls, value: object) -> str | None:
        return None if value is None else _device_id(value)

    @model_validator(mode="after")
    def shape(self) -> Self:
        search = self.operation is SpotifyOperation.SEARCH
        if search != (self.query is not None):
            raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
        if not search and (
            self.types != (SpotifyItemType.TRACK,)
            or self.limit != 5
            or self.offset != 0
        ):
            raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
        if self.media_uri is not None and self.operation is not SpotifyOperation.PLAY:
            raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
        if (
            self.operation
            in (
                SpotifyOperation.SEARCH,
                SpotifyOperation.PLAYBACK_STATE,
                SpotifyOperation.DEVICES,
            )
            and self.device_id is not None
        ):
            raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
        if self.operation is SpotifyOperation.TRANSFER and self.device_id is None:
            raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
        if self.operation is not SpotifyOperation.TRANSFER and self.transfer_play:
            raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
        if (self.operation is SpotifyOperation.VOLUME) != (
            self.volume_percent is not None
        ):
            raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
        return self


def _metadata(operation: SpotifyOperation) -> tuple[str, ToolRiskLevel, str]:
    risk = (
        ToolRiskLevel.P1
        if operation
        in (
            SpotifyOperation.SEARCH,
            SpotifyOperation.PLAYBACK_STATE,
            SpotifyOperation.DEVICES,
        )
        else ToolRiskLevel.P2
    )
    return "spotify." + operation.value, risk, risk.value + ": exact Spotify scope"


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def _resource(call: SpotifyCall) -> str:
    return _json(
        [
            "spotify-v1",
            call.operation.value,
            sha256(call.query.encode("utf-8")).hexdigest()
            if call.query is not None
            else None,
            [kind.value for kind in call.types],
            call.limit,
            call.offset,
            call.media_uri,
            call.device_id,
            call.volume_percent,
            call.transfer_play,
        ]
    )


def _scope(resource: str, operation: SpotifyOperation) -> SpotifyCall:
    data = json.loads(_text(resource, 2048))
    if (
        type(data) is not list
        or len(data) != 10
        or data[:2] != ["spotify-v1", operation.value]
    ):
        raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
    digest = data[2]
    if operation is SpotifyOperation.SEARCH:
        if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
    elif digest is not None:
        raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
    result = SpotifyCall(
        call_id="scope",
        operation=operation,
        query="scope" if digest is not None else None,
        types=data[3],
        limit=data[4],
        offset=data[5],
        media_uri=data[6],
        device_id=data[7],
        volume_percent=data[8],
        transfer_play=data[9],
    )
    canonical = json.loads(_resource(result))
    canonical[2] = digest
    if resource != _json(canonical):
        raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
    return result


class SpotifyIntent(_Contract):
    call_id: str
    operation: SpotifyOperation
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
            raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
        try:
            _scope(self.resource, self.operation)
        except Exception:
            raise SpotifyError(SpotifyErrorCode.INVALID_INPUT) from None
        return self


class SpotifyPermissionScope(_Contract):
    request_id: str
    actor_id: str
    tool_id: str
    action: str
    resource: str


class SpotifyItem(_Contract):
    _code = SpotifyErrorCode.INVALID_RESPONSE
    item_type: SpotifyItemType
    spotify_id: str
    uri: str
    name: str
    artist_names: tuple[str, ...] = ()
    is_playable: bool | None = Field(default=None, strict=True)

    @field_validator("item_type", mode="before")
    @classmethod
    def kind(cls, value: object) -> object:
        return _enum(value, SpotifyItemType)

    @field_validator("name", mode="before")
    @classmethod
    def name_value(cls, value: object) -> str:
        return _text(value, 512, empty=True)

    @field_validator("spotify_id", "uri", mode="before")
    @classmethod
    def identifiers(cls, value: object) -> str:
        return _text(value, 128)

    @field_validator("artist_names", mode="before")
    @classmethod
    def artists(cls, value: object) -> tuple[str, ...]:
        if type(value) not in (tuple, list) or len(cast(Sequence[object], value)) > 32:
            raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE)
        return tuple(
            _text(item, 512, empty=True) for item in cast(Sequence[object], value)
        )

    @model_validator(mode="after")
    def correlate(self) -> Self:
        if (
            re.fullmatch(r"[A-Za-z0-9]{22}", _text(self.spotify_id, 22)) is None
            or self.uri != "spotify:" + self.item_type.value + ":" + self.spotify_id
        ):
            raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE)
        if (
            self.item_type not in (SpotifyItemType.TRACK, SpotifyItemType.ALBUM)
            and self.artist_names
        ):
            raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE)
        return self


class SpotifyDevice(_Contract):
    _code = SpotifyErrorCode.INVALID_RESPONSE
    device_id: str | None
    name: str
    device_type: str
    is_active: bool = Field(strict=True)
    is_restricted: bool = Field(strict=True)
    volume_percent: Annotated[int, Field(strict=True, ge=0, le=100)] | None
    supports_volume: bool | None = Field(default=None, strict=True)

    @field_validator("device_id", mode="before")
    @classmethod
    def identity_value(cls, value: object) -> str | None:
        return None if value is None else _device_id(value)

    @field_validator("name", "device_type", mode="before")
    @classmethod
    def name_value(cls, value: object) -> str:
        return _text(value, 512, empty=True)


class SpotifyResponse(_Contract):
    _code = SpotifyErrorCode.INVALID_RESPONSE
    operation: SpotifyOperation
    items: tuple[SpotifyItem, ...] = ()
    devices: tuple[SpotifyDevice, ...] = ()
    playback_available: bool | None = Field(default=None, strict=True)
    is_playing: bool | None = Field(default=None, strict=True)
    progress_ms: Annotated[int, Field(strict=True, ge=0, le=2**53 - 1)] | None = None
    current_item_type: str | None = None
    current_item: SpotifyItem | None = None
    active_device: SpotifyDevice | None = None
    command_accepted: bool = Field(default=False, strict=True)

    @field_validator("items", mode="before")
    @classmethod
    def item_snapshots(cls, value: object) -> tuple[SpotifyItem, ...]:
        if type(value) not in (tuple, list) or len(cast(Sequence[object], value)) > 40:
            raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE)
        return tuple(
            SpotifyItem.model_validate(item) for item in cast(Sequence[object], value)
        )

    @field_validator("devices", mode="before")
    @classmethod
    def device_snapshots(cls, value: object) -> tuple[SpotifyDevice, ...]:
        if type(value) not in (tuple, list) or len(cast(Sequence[object], value)) > 64:
            raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE)
        return tuple(
            SpotifyDevice.model_validate(item) for item in cast(Sequence[object], value)
        )

    @field_validator("current_item", mode="before")
    @classmethod
    def current_snapshot(cls, value: object) -> SpotifyItem | None:
        return None if value is None else SpotifyItem.model_validate(value)

    @field_validator("active_device", mode="before")
    @classmethod
    def active_snapshot(cls, value: object) -> SpotifyDevice | None:
        return None if value is None else SpotifyDevice.model_validate(value)

    @field_validator("current_item_type", mode="before")
    @classmethod
    def current_type(cls, value: object) -> str | None:
        return None if value is None else _text(value, 32)

    @model_validator(mode="after")
    def shape(self) -> Self:
        playback = (
            self.playback_available,
            self.is_playing,
            self.progress_ms,
            self.current_item_type,
            self.current_item,
            self.active_device,
        )
        if self.operation is SpotifyOperation.SEARCH:
            if (
                self.devices
                or any(value is not None for value in playback)
                or self.command_accepted
            ):
                raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE)
        elif self.operation is SpotifyOperation.DEVICES:
            if (
                self.items
                or any(value is not None for value in playback)
                or self.command_accepted
            ):
                raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE)
        elif self.operation is SpotifyOperation.PLAYBACK_STATE:
            if (
                self.items
                or self.devices
                or self.command_accepted
                or self.playback_available is None
            ):
                raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE)
            if not self.playback_available and any(
                value is not None for value in playback[1:]
            ):
                raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE)
            if self.playback_available and self.is_playing is None:
                raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE)
            if (
                self.current_item is not None
                and self.current_item_type != self.current_item.item_type.value
            ):
                raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE)
        elif (
            self.items
            or self.devices
            or any(value is not None for value in playback)
            or not self.command_accepted
        ):
            raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE)
        return self


def _status(code: SpotifyErrorCode) -> ToolResultStatus:
    if code is SpotifyErrorCode.TIMEOUT:
        return ToolResultStatus.TIMEOUT
    if code is SpotifyErrorCode.CANCELLED:
        return ToolResultStatus.CANCELLED
    if code is SpotifyErrorCode.UNAVAILABLE:
        return ToolResultStatus.UNAVAILABLE_DEPENDENCY
    return ToolResultStatus.FAILURE


class SpotifyToolResult(_Contract):
    _code = SpotifyErrorCode.INVALID_RESPONSE
    intent: SpotifyIntent
    permission_scope: SpotifyPermissionScope
    permission_decision: PermissionDecision
    attempted: bool = Field(strict=True)
    status: ToolResultStatus
    response: SpotifyResponse | None = None
    error_code: SpotifyErrorCode | None = None
    retry_after_seconds: Annotated[int, Field(strict=True, ge=0, le=86400)] | None = (
        None
    )

    @field_validator("intent", mode="before")
    @classmethod
    def intent_snapshot(cls, value: object) -> SpotifyIntent:
        return SpotifyIntent.model_validate(value)

    @field_validator("permission_scope", mode="before")
    @classmethod
    def scope_snapshot(cls, value: object) -> SpotifyPermissionScope:
        return SpotifyPermissionScope.model_validate(value)

    @field_validator("permission_decision", mode="before")
    @classmethod
    def decision_snapshot(cls, value: object) -> PermissionDecision:
        return _external(value, PermissionDecision)

    @field_validator("response", mode="before")
    @classmethod
    def response_snapshot(cls, value: object) -> SpotifyResponse | None:
        return None if value is None else SpotifyResponse.model_validate(value)

    @field_validator("status", mode="before")
    @classmethod
    def status_value(cls, value: object) -> object:
        return _enum(value, ToolResultStatus)

    @field_validator("error_code", mode="before")
    @classmethod
    def error_value(cls, value: object) -> object:
        return None if value is None else _enum(value, SpotifyErrorCode)

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
            raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE)
        if d.status is not PermissionStatus.ALLOWED:
            if (
                self.attempted
                or self.status is not ToolResultStatus.DENIED
                or self.response is not None
                or self.error_code is not None
                or self.retry_after_seconds is not None
            ):
                raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE)
        elif not self.attempted:
            raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE)
        elif self.status is ToolResultStatus.SUCCESS:
            if (
                self.response is None
                or self.error_code is not None
                or self.retry_after_seconds is not None
                or self.response.operation is not i.operation
            ):
                raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE)
            bound = _scope(i.resource, i.operation)
            if i.operation is SpotifyOperation.SEARCH:
                for kind in SpotifyItemType:
                    values = [
                        item for item in self.response.items if item.item_type is kind
                    ]
                    if values and (
                        kind not in bound.types or len(values) > bound.limit
                    ):
                        raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE)
        elif (
            self.response is not None
            or self.error_code is None
            or self.status is not _status(self.error_code)
        ):
            raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE)
        if (
            self.retry_after_seconds is not None
            and self.error_code is not SpotifyErrorCode.RATE_LIMITED
        ):
            raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE)
        return self


class SpotifyAuthorization(_Contract):
    state: str
    authorization_url: str = Field(repr=False)

    @model_validator(mode="after")
    def shape(self) -> Self:
        if re.fullmatch(r"[A-Za-z0-9_-]{43,128}", self.state) is None:
            raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
        parts = urlsplit(self.authorization_url)
        if (
            parts.scheme != "https"
            or parts.netloc != "accounts.spotify.com"
            or parts.path != "/authorize"
            or parts.fragment
        ):
            raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
        return self


def spotify_tool_definitions() -> tuple[ToolDefinition, ...]:
    input_schema = SpotifyCall.model_json_schema()
    output_schema = SpotifyToolResult.model_json_schema()
    return tuple(
        ToolDefinition(
            tool_id=tool,
            description="Spotify "
            + operation.value
            + "; third-party text is untrusted.",
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
        for operation in SpotifyOperation
        for tool, risk, requirement in (_metadata(operation),)
    )


def _object(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE)
    return cast(dict[str, object], value)


def _array(value: object, limit: int) -> list[object]:
    if type(value) is not list or len(cast(list[object], value)) > limit:
        raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE)
    return cast(list[object], value)


def _item(value: object) -> SpotifyItem:
    data = _object(value)
    kind = data.get("type")
    artists = (
        _array(data.get("artists", []), 32)
        if kind in (SpotifyItemType.TRACK, SpotifyItemType.ALBUM)
        else []
    )
    return SpotifyItem.model_validate(
        dict(
            item_type=kind,
            spotify_id=data.get("id"),
            uri=data.get("uri"),
            name=data.get("name"),
            artist_names=tuple(_object(item).get("name") for item in artists),
            is_playable=data.get("is_playable"),
        )
    )


def _device(value: object) -> SpotifyDevice:
    data = _object(value)
    return SpotifyDevice.model_validate(
        dict(
            device_id=data.get("id"),
            name=data.get("name"),
            device_type=data.get("type"),
            is_active=data.get("is_active"),
            is_restricted=data.get("is_restricted"),
            volume_percent=data.get("volume_percent"),
            supports_volume=data.get("supports_volume"),
        )
    )


def _normalized(call: SpotifyCall, status: int, value: object) -> SpotifyResponse:
    op = call.operation
    if op is SpotifyOperation.PLAYBACK_STATE and status == 204:
        return SpotifyResponse(operation=op, playback_available=False)
    if op not in (
        SpotifyOperation.SEARCH,
        SpotifyOperation.PLAYBACK_STATE,
        SpotifyOperation.DEVICES,
    ):
        return SpotifyResponse(operation=op, command_accepted=True)
    data = _object(value)
    if op is SpotifyOperation.SEARCH:
        items = []
        for kind in call.types:
            page = _object(data.get(kind.value + "s"))
            if (
                type(page.get("limit")) is not int
                or page["limit"] != call.limit
                or type(page.get("offset")) is not int
                or page["offset"] != call.offset
            ):
                raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE)
            for entry in _array(page.get("items"), call.limit):
                if entry is None:
                    continue  # Spotify can return unavailable/null catalog entries.
                item = _item(entry)
                if item.item_type is not kind:
                    raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE)
                items.append(item)
        return SpotifyResponse(operation=op, items=tuple(items))
    if op is SpotifyOperation.DEVICES:
        return SpotifyResponse(
            operation=op,
            devices=tuple(_device(item) for item in _array(data.get("devices"), 64)),
        )
    raw_item = data.get("item")
    playback_kind = data.get("currently_playing_type")
    playback_item = None
    if raw_item is not None:
        raw = _object(raw_item)
        if raw.get("is_local") is True:
            playback_kind = "local_track"
        elif playback_kind in ("track", "episode"):
            playback_item = _item(raw)
            if playback_item.item_type.value != playback_kind:
                raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE)
    return SpotifyResponse.model_validate(
        dict(
            operation=op,
            playback_available=True,
            is_playing=data.get("is_playing"),
            progress_ms=data.get("progress_ms"),
            current_item_type=playback_kind,
            current_item=playback_item,
            active_device=None
            if data.get("device") is None
            else _device(data["device"]),
        )
    )


def _endpoint(
    call: SpotifyCall,
) -> tuple[str, str, dict[str, str], dict[str, object] | None]:
    op = call.operation
    params: dict[str, str] = {}
    body: dict[str, object] | None = None
    if op is SpotifyOperation.SEARCH:
        params = {
            "q": cast(str, call.query),
            "type": ",".join(kind.value for kind in call.types),
            "limit": str(call.limit),
            "offset": str(call.offset),
        }
        return "GET", _API + "/search", params, body
    if op is SpotifyOperation.DEVICES:
        return "GET", _API + "/me/player/devices", params, body
    if op is SpotifyOperation.PLAYBACK_STATE:
        return "GET", _API + "/me/player", params, body
    if op is SpotifyOperation.TRANSFER:
        return (
            "PUT",
            _API + "/me/player",
            params,
            {"device_ids": [call.device_id], "play": call.transfer_play},
        )
    if call.device_id is not None:
        params["device_id"] = call.device_id
    if op is SpotifyOperation.PLAY and call.media_uri is not None:
        body = (
            {"uris": [call.media_uri]}
            if call.media_uri.startswith("spotify:track:")
            else {"context_uri": call.media_uri}
        )
    if op is SpotifyOperation.VOLUME:
        params["volume_percent"] = str(call.volume_percent)
    return (
        "POST" if op in (SpotifyOperation.NEXT, SpotifyOperation.PREVIOUS) else "PUT",
        _API + "/me/player/" + op.value,
        params,
        body,
    )


def _load_json(data: bytes) -> object:
    def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
        if len({key for key, _ in values}) != len(values):
            raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE)
        return dict(values)

    value: object = json.loads(data, object_pairs_hook=pairs)
    stack = [(value, 0)]
    count = 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if depth > 32 or count > 10000:
            raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE)
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
                raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE)
            item.encode("utf-8")
        elif type(item) is float and not math.isfinite(item):
            raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE)
    return value


class SpotifyTool:
    """One caller-owned operation; account-link setup is a separate trusted method.

    Injected clients/stores/transports are trusted adapters. No ambient production
    URL override exists. Physical playback and already-entered store writes cannot
    be undone after abort; success reports only documented API command acceptance.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        policy: PermissionPolicy,
        store: SecretStore,
        *,
        client_id: str,
        redirect_uri: str,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        try:
            if (
                hasattr(self, "_engine")
                or type(registry) is not ToolRegistry
                or type(policy) is not PermissionPolicy
            ):
                raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
            if client is not None and (
                type(client) is not httpx.AsyncClient or transport is not None
            ):
                raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
            if transport is not None and not isinstance(
                transport, httpx.AsyncBaseTransport
            ):
                raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
            if not all(
                callable(getattr(store, name, None))
                for name in ("put", "resolve", "delete")
            ):
                raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
            accepted_id = _text(client_id, 128)
            if re.fullmatch(r"[A-Za-z0-9]{1,128}", accepted_id) is None:
                raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
            accepted_redirect = _redirect(redirect_uri)
            snapshot = ToolRegistry(registry.list_tools())
            for definition in spotify_tool_definitions():
                if snapshot.require(definition.tool_id) != definition:
                    raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
            engine = PermissionEngine(snapshot, _external(policy, PermissionPolicy))
        except Exception:
            raise SpotifyError(SpotifyErrorCode.INVALID_INPUT) from None
        self._registry, self._engine, self._store = snapshot, engine, store
        self._client_id, self._redirect = accepted_id, accepted_redirect
        self._transport, self._client = transport, client
        self._owned = client is None
        self._closed = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._active: str | None = None
        self._pending: tuple[str, SecretMaterial] | None = None
        self._expires: float | None = None

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
            raise SpotifyError(SpotifyErrorCode.WRONG_EVENT_LOOP)
        if self._closed and not closing:
            raise SpotifyError(SpotifyErrorCode.CLOSED)

    def _guard(self) -> None:
        task = asyncio.current_task()
        if self._closed or (task is not None and task.cancelling()):
            raise asyncio.CancelledError
        if self._expires is not None and self._loop is not None:
            if self._loop.time() >= self._expires:
                raise SpotifyError(SpotifyErrorCode.TIMEOUT)

    def _enter(self, operation_id: str) -> None:
        self._bind()
        self._guard()
        if self._active is not None:
            raise SpotifyError(SpotifyErrorCode.BUSY)
        self._active = operation_id

    def describe(self, call: SpotifyCall) -> SpotifyIntent:
        accepted = SpotifyCall.model_validate(call)
        tool, _, _ = _metadata(accepted.operation)
        definition = self._registry.require(tool)
        return SpotifyIntent(
            call_id=accepted.call_id,
            operation=accepted.operation,
            tool_id=definition.tool_id,
            action=accepted.operation.value,
            resource=_resource(accepted),
            risk_level=definition.risk_level,
            permission_requirement=definition.permission_requirement,
        )

    def begin_authorization(self) -> SpotifyAuthorization:
        if self._closed:
            raise SpotifyError(SpotifyErrorCode.CLOSED)
        if self._pending is not None or self._active is not None:
            raise SpotifyError(SpotifyErrorCode.BUSY)
        state = secrets.token_urlsafe(32)
        verifier = SecretMaterial(secrets.token_urlsafe(64))
        challenge = (
            base64.urlsafe_b64encode(
                sha256(verifier.reveal_text_for_trusted_use().encode("ascii")).digest()
            )
            .decode("ascii")
            .rstrip("=")
        )
        authorization = SpotifyAuthorization(
            state=state,
            authorization_url=_AUTHORIZE
            + "?"
            + urlencode(
                dict(
                    response_type="code",
                    client_id=self._client_id,
                    redirect_uri=self._redirect,
                    scope=" ".join(SPOTIFY_SCOPES),
                    state=state,
                    code_challenge_method="S256",
                    code_challenge=challenge,
                )
            ),
        )
        self._pending = (state, verifier)
        return authorization

    def discard_authorization(self) -> None:
        if self._pending is not None:
            self._pending[1].close()
            self._pending = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                verify=True,
                trust_env=False,
                follow_redirects=False,
                timeout=httpx.Timeout(10.0, connect=5.0, pool=5.0),
                transport=self._transport,
            )
        return self._client

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        body: dict[str, object] | None = None,
        form: dict[str, str] | None = None,
    ) -> tuple[int, object, int | None]:
        try:
            self._guard()
            async with self._http().stream(
                method,
                url,
                headers=headers,
                params=params,
                json=body,
                data=form,
                auth=None,
                follow_redirects=False,
                timeout=httpx.Timeout(10.0, connect=5.0, pool=5.0),
            ) as response:
                self._guard()
                status = response.status_code
                retry = response.headers.get("Retry-After", "")
                retry_after = (
                    int(retry)
                    if re.fullmatch(r"[0-9]{1,5}", retry) and int(retry) <= 86400
                    else None
                )
                if status != 200:
                    return status, None, retry_after  # Never parse raw failure bodies.
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    self._guard()
                    if len(content) + len(chunk) > _MAX_BYTES:
                        raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE)
                    content.extend(chunk)
                self._guard()
                value = _load_json(bytes(content))
                return status, value, retry_after
        except httpx.TimeoutException:
            self._guard()
            raise SpotifyError(SpotifyErrorCode.TIMEOUT) from None
        except httpx.HTTPError:
            self._guard()
            raise SpotifyError(SpotifyErrorCode.UNAVAILABLE) from None
        except SpotifyError:
            self._guard()
            raise
        except Exception:
            self._guard()
            raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE) from None

    def _secret(self, reference: SecretReference) -> str:
        try:
            material = self._store.resolve(reference)
            if type(material) is not SecretMaterial:
                raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE)
            with material:
                value = self._token_text(material.reveal_text_for_trusted_use())
            self._guard()
            return value
        except Exception:
            self._guard()
            raise SpotifyError(SpotifyErrorCode.SECRET_UNAVAILABLE) from None

    @staticmethod
    def _token_text(value: object) -> str:
        result = _text(value, 2560)
        if any(c.isspace() or not 33 <= ord(c) <= 126 for c in result):
            raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE)
        return result

    def _store_tokens(self, value: object, *, initial: bool) -> str:
        try:
            data = _object(value)
            access = self._token_text(data.get("access_token"))
            refresh = (
                None
                if "refresh_token" not in data
                else self._token_text(data["refresh_token"])
            )
            expiry = data.get("expires_in")
            scope = data.get("scope")
            if (
                data.get("token_type") != "Bearer"
                or type(expiry) is not int
                or not 1 <= expiry <= 604800
                or (initial and refresh is None)
                or (scope is None and initial)
                or (
                    scope is not None
                    and (
                        type(scope) is not str
                        or set(scope.split()) != set(SPOTIFY_SCOPES)
                        or len(scope.split()) != 2
                    )
                )
            ):
                raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE)
        except Exception:
            raise SpotifyError(SpotifyErrorCode.INVALID_RESPONSE) from None
        try:
            # Validate the whole response before either non-atomic store write.
            if refresh is not None:
                with SecretMaterial(refresh) as material:
                    self._store.put(_REFRESH, material)
                self._guard()
            with SecretMaterial(access) as material:
                self._store.put(_ACCESS, material)
            self._guard()
        except Exception:
            self._guard()
            raise SpotifyError(SpotifyErrorCode.SECRET_UNAVAILABLE) from None
        return access

    async def _exchange(self, form: dict[str, str], *, initial: bool) -> str:
        status, value, _ = await self._request("POST", _TOKEN, form=form)
        self._guard()
        if status != 200:
            raise SpotifyError(SpotifyErrorCode.OAUTH_FAILURE)
        return self._store_tokens(value, initial=initial)

    async def complete_authorization(
        self, operation_id: str, code: SecretMaterial, *, state: str, redirect_uri: str
    ) -> None:
        try:
            identity = _id(operation_id)
        except Exception:
            raise SpotifyError(SpotifyErrorCode.INVALID_INPUT) from None
        self._enter(identity)
        pending, self._pending = self._pending, None
        try:
            if pending is None:
                raise SpotifyError(SpotifyErrorCode.OAUTH_FAILURE)
            if (
                type(state) is not str
                or not state.isascii()
                or not secrets.compare_digest(state, pending[0])
            ):
                raise SpotifyError(SpotifyErrorCode.OAUTH_STATE_MISMATCH)
            try:
                if (
                    _redirect(redirect_uri) != self._redirect
                    or type(code) is not SecretMaterial
                ):
                    raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
                code_text = self._token_text(code.reveal_text_for_trusted_use())
            except Exception:
                raise SpotifyError(SpotifyErrorCode.INVALID_INPUT) from None
            async with asyncio.timeout(_SECONDS) as deadline:
                self._expires = deadline.when()
                await self._exchange(
                    dict(
                        grant_type="authorization_code",
                        client_id=self._client_id,
                        code=code_text,
                        redirect_uri=self._redirect,
                        code_verifier=pending[1].reveal_text_for_trusted_use(),
                    ),
                    initial=True,
                )
                self._guard()
                if deadline.expired():
                    raise TimeoutError
        except TimeoutError:
            raise SpotifyError(SpotifyErrorCode.TIMEOUT) from None
        finally:
            self._expires = None
            if pending is not None:
                pending[1].close()
            self._active = None

    async def execute(
        self,
        call: SpotifyCall,
        request: PermissionRequest,
        *,
        grants: Sequence[PermissionGrant] = (),
        user_decision: UserPermissionDecision | None = None,
    ) -> SpotifyToolResult:
        try:
            accepted = SpotifyCall.model_validate(call)
            intent = self.describe(accepted)
            req = _external(request, PermissionRequest)
            if (req.request_id, req.tool_id, req.action, req.resource) != (
                intent.call_id,
                intent.tool_id,
                intent.action,
                intent.resource,
            ):
                raise SpotifyError(SpotifyErrorCode.INVALID_INPUT)
            decision = self._engine.evaluate(
                req, grants=grants, user_decision=user_decision
            )
            scope = SpotifyPermissionScope(
                request_id=req.request_id,
                actor_id=req.actor_id,
                tool_id=req.tool_id,
                action=req.action,
                resource=req.resource,
            )
        except Exception:
            raise SpotifyError(SpotifyErrorCode.INVALID_INPUT) from None
        self._bind()
        evidence = dict(
            intent=intent, permission_scope=scope, permission_decision=decision
        )
        if decision.status is not PermissionStatus.ALLOWED:
            return SpotifyToolResult.model_validate(
                evidence | dict(attempted=False, status=ToolResultStatus.DENIED)
            )
        self._enter(accepted.call_id)
        try:
            code = None
            retry_after = None
            normalized = None
            try:
                async with asyncio.timeout(_SECONDS) as deadline:
                    self._expires = deadline.when()
                    token = self._secret(_ACCESS)
                    method, url, params, body = _endpoint(accepted)
                    status, value, retry_after = await self._request(
                        method,
                        url,
                        headers={"Authorization": "Bearer " + token},
                        params=params,
                        body=body,
                    )
                    self._guard()
                    if status == 401:
                        refreshed = await self._exchange(
                            dict(
                                grant_type="refresh_token",
                                client_id=self._client_id,
                                refresh_token=self._secret(_REFRESH),
                            ),
                            initial=False,
                        )
                        self._guard()
                        status, value, retry_after = await self._request(
                            method,
                            url,
                            headers={"Authorization": "Bearer " + refreshed},
                            params=params,
                            body=body,
                        )
                        self._guard()
                    expected = (
                        (200, 204)
                        if accepted.operation is SpotifyOperation.PLAYBACK_STATE
                        else (200,)
                        if intent.risk_level is ToolRiskLevel.P1
                        else (204,)
                    )
                    if status in expected:
                        normalized = _normalized(accepted, status, value)
                    else:
                        code = (
                            SpotifyErrorCode.UNAUTHORIZED
                            if status == 401
                            else (
                                SpotifyErrorCode.PREMIUM_OR_DEVICE_REQUIRED
                                if intent.risk_level is ToolRiskLevel.P2
                                else SpotifyErrorCode.FORBIDDEN
                            )
                            if status == 403
                            else SpotifyErrorCode.RATE_LIMITED
                            if status == 429
                            else SpotifyErrorCode.UNAVAILABLE
                            if status >= 500
                            else SpotifyErrorCode.INVALID_RESPONSE
                        )
                    self._guard()
                    if deadline.expired():
                        raise TimeoutError
            except SpotifyError as error:
                code = (
                    error.code
                    if type(error) is SpotifyError
                    and any(error.code is member for member in SpotifyErrorCode)
                    else SpotifyErrorCode.INVALID_RESPONSE
                )
            except TimeoutError:
                code = SpotifyErrorCode.TIMEOUT
            except Exception:
                code = SpotifyErrorCode.INVALID_RESPONSE
            finally:
                self._expires = None
            self._guard()
            if code is not None:
                return SpotifyToolResult.model_validate(
                    evidence
                    | dict(
                        attempted=True,
                        status=_status(code),
                        error_code=code,
                        retry_after_seconds=retry_after
                        if code is SpotifyErrorCode.RATE_LIMITED
                        else None,
                    )
                )
            return SpotifyToolResult.model_validate(
                evidence
                | dict(
                    attempted=True, status=ToolResultStatus.SUCCESS, response=normalized
                )
            )
        finally:
            self._active = None

    async def aclose(self) -> None:
        self._bind(closing=True)
        self._closed = True
        self.discard_authorization()
        if self._owned and self._client is not None:
            try:
                async with asyncio.timeout(_SECONDS):
                    await self._client.aclose()
            except Exception:
                raise SpotifyError(SpotifyErrorCode.CLEANUP_FAILURE) from None
        # An in-flight caller retains its ID until it actually unregisters.
