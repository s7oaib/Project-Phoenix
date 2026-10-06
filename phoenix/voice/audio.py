"""Canonical caller-supplied PCM evidence, without capture, conversion or I/O.

Audio is sensitive untrusted data. Explicit serialization/byte access returns
audio to the caller; neither path should be logged or persisted. No cache exists.
"""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    ValidationError,
    field_validator,
    model_validator,
)

__all__ = ("AudioEncoding", "PcmAudioFrame")


class AudioEncoding(StrEnum):
    PCM_S16LE = "pcm_s16le"


class _AudioValidationError(Exception):
    """Safe PCM admission failure without Pydantic input diagnostics."""


def _identifier(value: object, bound: int = 256) -> str:
    # Reject controls before trimming so normalization cannot hide them.
    if type(value) is not str or any(
        ord(char) < 32 or 127 <= ord(char) <= 159 for char in value
    ):
        raise ValueError("invalid_input")
    text = value.strip()
    if not text or len(text) > bound:
        raise ValueError("invalid_input")
    text.encode("utf-8")
    return text


def _stored_values(value: object, cls: type[BaseModel]) -> object:
    if isinstance(value, BaseModel):
        if type(value) is not cls:
            raise ValueError("invalid_input")
        extras = value.__pydantic_extra__
        if extras is not None and (type(extras) is not dict or extras):
            raise ValueError("invalid_input")
        fields = vars(value)
        if set(fields) != set(cls.model_fields):
            raise ValueError("invalid_input")
        return dict(fields)
    return value


def _encoding(value: object) -> AudioEncoding:
    if value is AudioEncoding.PCM_S16LE or (
        type(value) is str and value == "pcm_s16le"
    ):
        return AudioEncoding.PCM_S16LE
    raise ValueError("invalid_input")


def _format_integer(value: object, expected: int) -> int:
    if type(value) is not int or value != expected:
        raise ValueError("invalid_input")
    return value


def _pcm(value: object) -> bytes:
    # Bound encoded input before allocating decoded storage.
    if type(value) is not str or not value or len(value) > 42668:
        raise ValueError("invalid_input")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("invalid_input") from None
    if (
        not decoded
        or len(decoded) > 32000
        or len(decoded) % 2
        or base64.b64encode(decoded).decode("ascii") != value
    ):
        raise ValueError("invalid_input")
    return decoded


class PcmAudioFrame(BaseModel):
    """Frozen local audio snapshot; captured_at is caller evidence, not a clock."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
        hide_input_in_errors=True,
    )

    stream_id: str
    sequence: Annotated[int, Field(strict=True, ge=0, le=2147483647)]
    captured_at: AwareDatetime
    sample_rate_hz: int
    channels: int
    sample_width_bytes: int
    encoding: AudioEncoding
    pcm_base64: str = Field(repr=False)
    pcm_sha256: str

    @model_validator(mode="wrap")
    @classmethod
    def exact_snapshot(
        cls, value: object, handler: ModelWrapValidatorHandler[Self]
    ) -> Self:
        try:
            return handler(_stored_values(value, cls))
        except (
            ValidationError,
            ValueError,
            TypeError,
            AttributeError,
            OverflowError,
            RecursionError,
        ):
            # Discard Pydantic diagnostic input/extra names, including raw audio.
            raise _AudioValidationError("invalid_input") from None

    @field_validator("stream_id", mode="before")
    @classmethod
    def identifier(cls, value: object) -> str:
        return _identifier(value)

    @field_validator("sequence", mode="before")
    @classmethod
    def ordinary_integer(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("invalid_input")
        return value

    @field_validator("captured_at", mode="before")
    @classmethod
    def timestamp(cls, value: object) -> object:
        if type(value) not in (datetime, str):
            raise ValueError("invalid_input")
        return value

    @field_validator("captured_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @field_validator("sample_rate_hz", "channels", "sample_width_bytes", mode="before")
    @classmethod
    def format_integer(cls, value: object) -> int:
        # Integrity below freezes the values; this prevents coercion.
        if type(value) is not int:
            raise ValueError("invalid_input")
        return value

    @field_validator("encoding", mode="before")
    @classmethod
    def encoding_value(cls, value: object) -> AudioEncoding:
        return _encoding(value)

    @field_validator("pcm_base64", mode="before")
    @classmethod
    def canonical_pcm(cls, value: object) -> str:
        _pcm(value)
        assert type(value) is str
        return value

    @field_validator("pcm_sha256", mode="before")
    @classmethod
    def digest_text(cls, value: object) -> str:
        if (
            type(value) is not str
            or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)
        ):
            raise ValueError("invalid_input")
        return value

    @model_validator(mode="after")
    def integrity(self) -> Self:
        try:
            _format_integer(self.sample_rate_hz, 16000)
            _format_integer(self.channels, 1)
            _format_integer(self.sample_width_bytes, 2)
            if (
                type(self.pcm_sha256) is not str
                or self.pcm_sha256 != sha256(_pcm(self.pcm_base64)).hexdigest()
            ):
                raise ValueError("invalid_input")
        except ValueError:
            raise _AudioValidationError("invalid_input") from None
        return self

    def pcm_bytes_for_provider(self) -> bytes:
        """Revalidate stored evidence, then return exact bytes without caching/I/O."""
        frame = PcmAudioFrame.model_validate(self)
        return _pcm(frame.pcm_base64)
