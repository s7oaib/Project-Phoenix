"""PHX-060 synthetic contract, adversarial, substitutability and isolation proof."""

from __future__ import annotations

import ast
import asyncio
import base64
import builtins
import ctypes
import importlib.util
import inspect
import logging
import os
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import traceback
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta, timezone
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any, Never, TypeVar, cast
from uuid import UUID

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import BaseModel, ValidationError

from phoenix.core.event_bus import EventBus
from phoenix.core.events import EventType
from phoenix.models.provider import CancellationResult, CancellationStatus
from phoenix.voice import audio, wake
from phoenix.voice.audio import AudioEncoding, PcmAudioFrame
from phoenix.voice.wake import (
    DEFAULT_WAKE_PHRASE,
    WakePhrase,
    WakeWordCapabilities,
    WakeWordDetection,
    WakeWordProvider,
    WakeWordProviderError,
    WakeWordProviderErrorCode,
    WakeWordRequest,
)

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "e58d304df3a2097ae03e0b31941c9cf487a584f9"
NOW = datetime(2026, 10, 7, 12, tzinfo=UTC)
PCM = b"\x01\x02\x03\x04"
BAD = (None, True, 1, 1.5, b"text", object(), "\0", "\ud800")
BAD_IDS = BAD + ("", " ", "x\n", "\tx", "x\x7f", "x\x85", "x" * 257)
T = TypeVar("T")


def run(value: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(value)


def frame(data: bytes = PCM, /, **changes: Any) -> PcmAudioFrame:
    return PcmAudioFrame.model_validate(
        dict(
            stream_id="Stream-A",
            sequence=0,
            captured_at=NOW,
            sample_rate_hz=16000,
            channels=1,
            sample_width_bytes=2,
            encoding="pcm_s16le",
            pcm_base64=base64.b64encode(data).decode("ascii"),
            pcm_sha256=sha256(data).hexdigest(),
        )
        | changes
    )


def phrase(**changes: Any) -> WakePhrase:
    return WakePhrase.model_validate(DEFAULT_WAKE_PHRASE.model_dump() | changes)


def capabilities(**changes: Any) -> WakeWordCapabilities:
    return WakeWordCapabilities.model_validate(
        dict(
            provider_id="Provider-A",
            supported_phrases=(DEFAULT_WAKE_PHRASE,),
            sample_rate_hz=16000,
            channels=1,
            sample_width_bytes=2,
            encoding="pcm_s16le",
            supports_cancellation=True,
        )
        | changes
    )


def request(**changes: Any) -> WakeWordRequest:
    return WakeWordRequest.model_validate(
        dict(operation_id="Operation-A", phrase=DEFAULT_WAKE_PHRASE, frames=(frame(),))
        | changes
    )


def detection(
    incoming: WakeWordRequest | None = None, /, **changes: Any
) -> WakeWordDetection:
    incoming = incoming or request()
    return WakeWordDetection.model_validate(
        dict(
            operation_id=incoming.operation_id,
            provider_id="Provider-A",
            phrase_id=incoming.phrase.phrase_id,
            stream_id=incoming.frames[0].stream_id,
            start_sequence=incoming.frames[0].sequence,
            end_sequence=incoming.frames[-1].sequence,
            detected=True,
            score=0.25,
        )
        | changes
    )


def fail(call: Callable[[], object], code: str | None = None) -> None:
    with pytest.raises((audio._AudioValidationError, WakeWordProviderError)) as caught:
        call()
    assert str(caught.value) in ("invalid_input", "invalid_response")
    if code is not None:
        assert str(caught.value) == code


FACTORIES: tuple[Callable[..., BaseModel], ...] = (
    frame,
    phrase,
    capabilities,
    request,
    detection,
)
FIELDS = (
    "stream_id sequence captured_at sample_rate_hz channels sample_width_bytes "
    "encoding pcm_base64 pcm_sha256",
    "phrase_id text",
    "provider_id supported_phrases sample_rate_hz channels sample_width_bytes "
    "encoding supports_cancellation",
    "operation_id phrase frames",
    "operation_id provider_id phrase_id stream_id start_sequence end_sequence "
    "detected score",
)


@pytest.mark.parametrize("factory,fields", zip(FACTORIES, FIELDS))
def test_exact_fields_frozen_extras_and_json(
    factory: Callable[..., BaseModel], fields: str
) -> None:
    value = factory()
    assert set(type(value).model_fields) == set(fields.split())
    assert type(value).model_validate_json(value.model_dump_json()) == value
    assert type(value).model_validate(value) is not value
    field = fields.split()[0]
    with pytest.raises(ValidationError):
        setattr(value, field, "altered")
    fail(lambda: type(value).model_validate(value.model_dump() | {"approved": True}))


def test_audio_encoding_default_phrase_and_owner() -> None:
    assert [member.value for member in AudioEncoding] == ["pcm_s16le"]
    assert DEFAULT_WAKE_PHRASE.phrase_id == "rise_phoenix"
    assert DEFAULT_WAKE_PHRASE.text == "Rise, Phoenix"
    assert "voice" in (wake.__doc__ or "") and "UTA" in (wake.__doc__ or "")
    for model in (WakePhrase, WakeWordCapabilities, WakeWordRequest, WakeWordDetection):
        assert (
            "alias" not in model.model_fields and "owner_id" not in model.model_fields
        )
    with pytest.raises(ValidationError):
        DEFAULT_WAKE_PHRASE.text = "changed"


@pytest.mark.parametrize(
    "factory,field",
    [
        (frame, "stream_id"),
        (phrase, "phrase_id"),
        (phrase, "text"),
        (capabilities, "provider_id"),
        (request, "operation_id"),
        (detection, "operation_id"),
        (detection, "provider_id"),
        (detection, "phrase_id"),
        (detection, "stream_id"),
    ],
)
@pytest.mark.parametrize("value", BAD_IDS)
def test_strict_control_free_ids(
    factory: Callable[..., BaseModel], field: str, value: object
) -> None:
    fail(lambda: factory(**{field: value}))


@pytest.mark.parametrize(
    "factory,field,bound",
    [
        (frame, "stream_id", 256),
        (phrase, "phrase_id", 128),
        (phrase, "text", 128),
        (capabilities, "provider_id", 256),
        (request, "operation_id", 256),
        (detection, "operation_id", 256),
        (detection, "provider_id", 256),
        (detection, "phrase_id", 128),
        (detection, "stream_id", 256),
    ],
)
def test_ids_normalization_unicode_and_bounds(
    factory: Callable[..., BaseModel], field: str, bound: int
) -> None:
    assert (
        getattr(factory(**{field: "  Case-Ελληνικά 🌐  "}), field) == "Case-Ελληνικά 🌐"
    )
    assert getattr(factory(**{field: "a" * bound}), field) == "a" * bound
    fail(lambda: factory(**{field: "a" * (bound + 1)}))


def test_phrase_outer_trim_only_no_fuzzy_matching() -> None:
    assert phrase(text="  Rise,  PHOENIX!  ").text == "Rise,  PHOENIX!"
    assert phrase(text="Rise Phoenix") != DEFAULT_WAKE_PHRASE
    assert phrase(text="rise, phoenix") != DEFAULT_WAKE_PHRASE
    assert phrase(text="Phoenix") != DEFAULT_WAKE_PHRASE


@pytest.mark.parametrize("value", [-1, 2147483648, True, False, 1.0, "0", None])
def test_invalid_frame_sequence(value: object) -> None:
    fail(lambda: frame(sequence=value))


@pytest.mark.parametrize("value", [0, 2147483647])
def test_frame_sequence_endpoints(value: int) -> None:
    assert frame(sequence=value).sequence == value


@pytest.mark.parametrize(
    "value",
    [datetime(2026, 1, 1), "2026-01-01T00:00:00", 0, 1.5, True, None, "invalid"],
)
def test_timestamp_must_be_explicit_aware(value: object) -> None:
    fail(lambda: frame(captured_at=value))


def test_timestamp_utc_and_no_default() -> None:
    local = NOW.astimezone(timezone(timedelta(hours=5, minutes=30)))
    assert frame(captured_at=local).captured_at == NOW
    assert frame(captured_at=local).captured_at.tzinfo is UTC
    assert frame(captured_at=local.isoformat()).captured_at == NOW
    values = frame().model_dump()
    del values["captured_at"]
    fail(lambda: PcmAudioFrame.model_validate(values))


@pytest.mark.parametrize("factory", [frame, capabilities])
@pytest.mark.parametrize(
    "field,expected",
    [("sample_rate_hz", 16000), ("channels", 1), ("sample_width_bytes", 2)],
)
@pytest.mark.parametrize("variant", ["float", "string", "bool", "other", "none"])
def test_fixed_audio_format_no_coercion(
    factory: Callable[..., BaseModel], field: str, expected: int, variant: str
) -> None:
    value = {
        "float": float(expected),
        "string": str(expected),
        "bool": True,
        "other": expected + 1,
        "none": None,
    }[variant]
    fail(lambda: factory(**{field: value}))


class Foreign(StrEnum):
    PCM = "pcm_s16le"
    ERROR = "invalid_input"


@pytest.mark.parametrize("factory", [frame, capabilities])
@pytest.mark.parametrize(
    "value",
    [
        "wav",
        "mp3",
        "aac",
        "opus",
        "pcm_f32le",
        "PCM_S16LE",
        b"pcm_s16le",
        Foreign.PCM,
        None,
        True,
    ],
)
def test_audio_encoding_rejects_other_vocabulary_and_foreign_enum(
    factory: Callable[..., BaseModel], value: object
) -> None:
    fail(lambda: factory(encoding=value))


@pytest.mark.parametrize(
    "value",
    [
        "",
        "AQ==",
        "A",
        "%%%",
        "AQIDBA",
        "AQIDBA===",
        "AQIDBA==\n",
        " AQIDBA==",
        "AQID BA==",
        "AQIDBB==",
        "-_8=",
        "//9=",
        b"AQIDBA==",
        None,
        True,
        "é",
        "\ud800",
    ],
)
def test_base64_empty_odd_malformed_noncanonical_and_urlsafe(value: object) -> None:
    fail(lambda: frame(pcm_base64=value))


@pytest.mark.parametrize("size", [2, 4, 31998, 32000])
def test_frame_size_admission_and_exact_bytes(size: int) -> None:
    data = bytes(range(256)) * (size // 256) + bytes(range(size % 256))
    value = frame(data)
    assert value.pcm_bytes_for_provider() == data
    assert value.pcm_sha256 == sha256(data).hexdigest()
    assert "pcm_base64" not in repr(value)
    assert value.pcm_base64 not in repr(value) and value.pcm_base64 not in str(value)
    assert "pcm_base64" not in repr(request(frames=(value,)))
    assert vars(value).keys() == PcmAudioFrame.model_fields.keys()


@pytest.mark.parametrize("size", [0, 1, 3, 32001, 32002, 96000])
def test_frame_rejects_invalid_sizes_without_repair(size: int) -> None:
    fail(lambda: frame(b"a" * size))


@pytest.mark.parametrize(
    "value",
    [
        "",
        "a" * 63,
        "a" * 65,
        "a" * 64,
        sha256(PCM).hexdigest().upper(),
        sha256(PCM).hexdigest().encode(),
        None,
        True,
    ],
)
def test_hash_exact_lowercase(value: object) -> None:
    fail(lambda: frame(pcm_sha256=value))


@pytest.mark.parametrize("count", [1, 32])
def test_capability_phrase_count_detached_snapshot(count: int) -> None:
    phrases = [phrase(phrase_id=f"p-{i}", text=f"phrase {i}") for i in range(count)]
    value = capabilities(supported_phrases=phrases)
    phrases.clear()
    assert (
        type(value.supported_phrases) is tuple and len(value.supported_phrases) == count
    )
    assert value.supported_phrases[0].phrase_id == "p-0"


@pytest.mark.parametrize("value", [[], (), None, {}, "phrase", {1}, (phrase(),) * 33])
def test_capability_invalid_phrase_collections(value: object) -> None:
    fail(lambda: capabilities(supported_phrases=value))


@pytest.mark.parametrize(
    "second",
    [
        {"phrase_id": " rise_phoenix ", "text": "different"},
        {"phrase_id": "different", "text": "Rise, Phoenix"},
    ],
)
def test_duplicate_phrase_id_or_exact_text(second: dict[str, str]) -> None:
    fail(lambda: capabilities(supported_phrases=(phrase(), phrase(**second))))


def test_capability_does_not_semantically_deduplicate_or_select() -> None:
    value = capabilities(
        supported_phrases=(
            phrase(),
            phrase(phrase_id="RISE_PHOENIX", text="rise, phoenix"),
        )
    )
    assert len(value.supported_phrases) == 2
    value.validate_provider(" Provider-A ")
    fail(lambda: value.validate_provider("different"), "invalid_response")
    fail(lambda: value.validate_provider(True), "invalid_input")  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [0, 1, "true", "false", None, 0.0])
@pytest.mark.parametrize(
    "factory,field", [(capabilities, "supports_cancellation"), (detection, "detected")]
)
def test_strict_booleans(
    factory: Callable[..., BaseModel], field: str, value: object
) -> None:
    fail(lambda: factory(**{field: value}))


def test_request_id_default_and_caller_identity() -> None:
    first = WakeWordRequest(phrase=phrase(), frames=(frame(),))
    second = WakeWordRequest(phrase=phrase(), frames=(frame(),))
    assert (
        UUID(first.operation_id).version == 4
        and first.operation_id != second.operation_id
    )
    assert request(operation_id="  Stable-ID  ").operation_id == "Stable-ID"


@pytest.mark.parametrize("count", [1, 32])
def test_request_frame_count_order_snapshot(count: int) -> None:
    frames = [frame(sequence=i) for i in range(count)]
    value = request(frames=frames)
    frames.reverse()
    assert type(value.frames) is tuple
    assert tuple(f.sequence for f in value.frames) == tuple(range(count))
    assert value.frames[0] is not frames[-1]
    assert value.phrase is not DEFAULT_WAKE_PHRASE


@pytest.mark.parametrize("value", [[], (), None, {}, "audio", {1}])
def test_invalid_request_frame_collection(value: object) -> None:
    fail(lambda: request(frames=value))


def test_request_33_frames_rejected() -> None:
    fail(lambda: request(frames=tuple(frame(sequence=i) for i in range(33))))


@pytest.mark.parametrize(
    "second",
    [
        {"stream_id": "Stream-B", "sequence": 1},
        {"sequence": 0},
        {"sequence": 2},
        {"sequence": 1, "captured_at": NOW - timedelta(microseconds=1)},
    ],
)
def test_window_rejects_stream_gap_duplicate_or_timestamp_change(
    second: dict[str, object],
) -> None:
    fail(lambda: request(frames=(frame(), frame(**second))))


def test_window_order_and_equal_or_increasing_timestamps() -> None:
    fail(lambda: request(frames=(frame(sequence=1), frame(sequence=0))))
    original = frame()
    fail(lambda: request(frames=(original, original)))
    value = request(
        frames=(
            frame(sequence=4),
            frame(sequence=5),
            frame(sequence=6, captured_at=NOW + timedelta(seconds=1)),
        )
    )
    assert tuple(f.sequence for f in value.frames) == (4, 5, 6)
    assert value.frames[1].captured_at == value.frames[0].captured_at


def test_request_total_exact_96000_and_no_truncation() -> None:
    frames = tuple(frame(b"a" * 32000, sequence=i) for i in range(3))
    value = request(frames=frames)
    assert sum(len(f.pcm_bytes_for_provider()) for f in value.frames) == 96000
    assert value.frames == frames
    fail(lambda: request(frames=frames + (frame(sequence=3),)))


@pytest.mark.parametrize("field", ["start_sequence", "end_sequence"])
@pytest.mark.parametrize("value", [-1, True, False, 0.0, "0", None])
def test_detection_sequence_strict(field: str, value: object) -> None:
    fail(lambda: detection(**{field: value}))


def test_detection_range_and_large_nonnegative_evidence() -> None:
    fail(lambda: detection(start_sequence=2, end_sequence=1))
    assert detection(start_sequence=0, end_sequence=0).start_sequence == 0
    assert detection(start_sequence=2**40, end_sequence=2**40).end_sequence == 2**40


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        0,
        1,
        "0.5",
        None,
        float("nan"),
        float("inf"),
        -float("inf"),
        -0.01,
        1.01,
    ],
)
def test_detection_invalid_score(value: object) -> None:
    fail(lambda: detection(score=value))


@pytest.mark.parametrize("score", [0.0, 0.5, 1.0])
@pytest.mark.parametrize("detected", [False, True])
def test_score_is_evidence_and_never_overrides_flag(
    score: float, detected: bool
) -> None:
    value = detection(score=score, detected=detected)
    value.validate_request(request(), "Provider-A")
    assert value.score == score and value.detected is detected


@pytest.mark.parametrize(
    "changes",
    [
        {"operation_id": "other"},
        {"provider_id": "other"},
        {"phrase_id": "other"},
        {"stream_id": "other"},
        {"start_sequence": 1, "end_sequence": 1},
        {"end_sequence": 1},
    ],
)
def test_detection_exact_correlation(changes: dict[str, object]) -> None:
    fail(
        lambda: detection(**changes).validate_request(request(), "Provider-A"),
        "invalid_response",
    )


def test_detection_range_equals_full_request_and_invalid_request_fails() -> None:
    incoming = request(frames=(frame(sequence=9), frame(sequence=10)))
    detection(incoming).validate_request(incoming, "Provider-A")
    fail(
        lambda: detection(incoming, end_sequence=9).validate_request(
            incoming, "Provider-A"
        )
    )
    fail(lambda: detection().validate_request(None, "Provider-A"), "invalid_input")  # type: ignore[arg-type]
    fail(lambda: detection().validate_request(request(), "\nprovider"), "invalid_input")


@pytest.mark.parametrize("factory", FACTORIES)
@pytest.mark.parametrize(
    "attack",
    [
        "subclass",
        "copy-extra",
        "construct-extra",
        "extra",
        "collision",
        "extra-type",
        "missing-extra-state",
        "missing-field",
        "invalid-field",
    ],
)
def test_exact_stored_value_subclass_and_extra_attacks(
    factory: Callable[..., BaseModel], attack: str
) -> None:
    value = factory()
    cls = type(value)
    first = next(iter(cls.model_fields))
    if attack == "subclass":
        value = cast(type[BaseModel], type("Extension", (cls,), {})).model_construct(
            **vars(value)
        )
    elif attack == "copy-extra":
        value = value.model_copy(update={"approved": True})
    elif attack == "construct-extra":
        value = value.model_copy()
        vars(value)["approved"] = True
    elif attack in ("extra", "collision", "extra-type"):
        object.__setattr__(
            value,
            "__pydantic_extra__",
            {first: "forged"}
            if attack == "collision"
            else {"approved": True}
            if attack == "extra"
            else [],
        )
    elif attack == "missing-extra-state":
        object.__delattr__(value, "__pydantic_extra__")
    elif attack == "missing-field":
        del vars(value)[first]
    else:
        vars(value)[first] = True
    fail(lambda: cls.model_validate(value))


@pytest.mark.parametrize("attack", ["bad-sequence", "bad-hash", "extra", "subclass"])
def test_nested_frame_exact_revalidation(attack: str) -> None:
    value = frame()
    if attack == "bad-sequence":
        value = value.model_copy(update={"sequence": True})
    elif attack == "bad-hash":
        value = value.model_copy(update={"pcm_sha256": "a" * 64})
    elif attack == "extra":
        value = value.model_copy(update={"approved": True})
    else:
        value = cast(
            type[PcmAudioFrame], type("Extension", (PcmAudioFrame,), {})
        ).model_construct(**vars(value))
    fail(lambda: request(frames=(value,)))
    fail(value.pcm_bytes_for_provider)


@pytest.mark.parametrize("attack", ["bad-id", "extra", "subclass"])
def test_nested_phrase_exact_revalidation(attack: str) -> None:
    value = phrase()
    if attack == "bad-id":
        value = value.model_copy(update={"phrase_id": True})
    elif attack == "extra":
        value = value.model_copy(update={"approved": True})
    else:
        value = cast(
            type[WakePhrase], type("Extension", (WakePhrase,), {})
        ).model_construct(**vars(value))
    fail(lambda: request(phrase=value))
    fail(lambda: capabilities(supported_phrases=(value,)))


def test_ordinary_types_without_implicit_conversion() -> None:
    class Text(str):
        pass

    class Number(int):
        pass

    class Score(float):
        pass

    class NoCoercion:
        def __str__(self) -> str:
            raise AssertionError("Implicit text conversion")

        def __bytes__(self) -> bytes:
            raise AssertionError("Implicit byte conversion")

    fail(lambda: frame(stream_id=Text("stream")))
    fail(lambda: frame(sequence=Number(0)))
    fail(lambda: frame(pcm_base64=Text(base64.b64encode(PCM).decode())))
    fail(lambda: frame(pcm_sha256=Text(sha256(PCM).hexdigest())))
    fail(lambda: phrase(text=NoCoercion()))
    fail(lambda: frame(pcm_base64=NoCoercion()))
    fail(lambda: detection(score=Score(0.5)))


@pytest.mark.parametrize("target", ["frame", "phrase"])
def test_nested_colliding_extra_storage_is_rejected(target: str) -> None:
    value = frame() if target == "frame" else phrase()
    field = "sequence" if target == "frame" else "text"
    object.__setattr__(value, "__pydantic_extra__", {field: "forged"})
    if target == "frame":
        fail(lambda: request(frames=(value,)))
    else:
        fail(lambda: request(phrase=value))
        fail(lambda: capabilities(supported_phrases=(value,)))


def test_helpers_revalidate_forged_stored_models() -> None:
    bad_detection = detection().model_copy(update={"score": True})
    fail(
        lambda: bad_detection.validate_request(request(), "Provider-A"),
        "invalid_response",
    )
    bad_request = request().model_copy(update={"operation_id": True})
    fail(
        lambda: detection().validate_request(bad_request, "Provider-A"), "invalid_input"
    )
    bad_capabilities = capabilities().model_copy(
        update={"supports_cancellation": True, "approved": True}
    )
    fail(lambda: bad_capabilities.validate_provider("Provider-A"), "invalid_response")


@pytest.mark.parametrize("factory", FACTORIES)
@pytest.mark.parametrize(
    "field",
    [
        "threshold",
        "model_path",
        "device_id",
        "api_key",
        "microphone_handle",
        "callback",
        "tool_id",
        "permission",
        "risk",
        "approval",
        "transcript",
        "command_text",
        "speaker_identity",
        "user_identity",
        "language",
        "action",
        "health",
        "startup_status",
        "trust",
        "verified",
    ],
)
def test_contracts_have_no_provider_options_or_authority(
    factory: Callable[..., BaseModel], field: str
) -> None:
    fail(lambda: type(factory()).model_validate(factory().model_dump() | {field: True}))


def test_error_vocabulary_exact() -> None:
    assert [code.value for code in WakeWordProviderErrorCode] == [
        "invalid_input",
        "invalid_response",
        "unsupported_phrase",
        "unsupported_audio",
        "unavailable",
        "timeout",
        "busy",
        "closed",
        "wrong_event_loop",
        "cleanup_failure",
    ]


@pytest.mark.parametrize("code", list(WakeWordProviderErrorCode))
def test_safe_provider_errors(code: WakeWordProviderErrorCode) -> None:
    error = WakeWordProviderError(code)
    assert (
        str(error) == code.value and error.args == (code.value,) and error.code is code
    )


@pytest.mark.parametrize("code", ["invalid_input", Foreign.ERROR, None, True, object()])
def test_foreign_error_codes_rejected(code: object) -> None:
    with pytest.raises(ValueError, match="^invalid_input$"):
        WakeWordProviderError(code)  # type: ignore[arg-type]


def test_validation_privacy_formatted_chains_and_no_logging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    marker = "PRIVATE-AUDIO-MARKER"
    encoded = base64.b64encode(marker.encode()).decode()
    for factory, changes in (
        (frame, {"pcm_base64": marker}),
        (frame, {"pcm_base64": encoded, "pcm_sha256": "bad"}),
        (request, {marker: encoded}),
        (detection, {marker: encoded}),
    ):
        try:
            factory(**changes)
        except (audio._AudioValidationError, WakeWordProviderError) as error:
            formatted = "".join(traceback.format_exception(error))
            assert marker not in formatted and encoded not in formatted
            assert error.__suppress_context__
        else:
            pytest.fail("Expected safe admission failure")
    detection(detected=True, score=1.0).validate_request(request(), "Provider-A")
    assert caplog.records == []


class ImmediateProvider(WakeWordProvider):
    """Independent test fake A, with no active resource or inference engine."""

    @property
    def active_operation_ids(self) -> tuple[str, ...]:
        return ()

    async def capabilities(self) -> WakeWordCapabilities:
        return capabilities(provider_id=self.provider_id, supports_cancellation=False)

    async def detect(self, incoming: WakeWordRequest) -> WakeWordDetection:
        accepted = WakeWordRequest.model_validate(incoming)
        if accepted.phrase != DEFAULT_WAKE_PHRASE:
            raise WakeWordProviderError(WakeWordProviderErrorCode.UNSUPPORTED_PHRASE)
        return detection(
            accepted, provider_id=self.provider_id, detected=False, score=1.0
        )

    async def cancel(self, operation_id: str) -> CancellationResult:
        return CancellationResult(
            operation_id=operation_id,
            provider_id=self.provider_id,
            status=CancellationStatus.UNSUPPORTED,
        )

    async def aclose(self) -> None:
        self.did_close = True


class ControlledProvider(WakeWordProvider):
    """Independent test fake B with caller-owned operation-scoped cancellation."""

    def __init__(self, provider_id: str) -> None:
        super().__init__(provider_id)
        self.pending: dict[str, asyncio.Task[Any]] = {}
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.did_close = False

    @property
    def active_operation_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.pending))

    async def capabilities(self) -> WakeWordCapabilities:
        return capabilities(provider_id=self.provider_id)

    async def detect(self, incoming: WakeWordRequest) -> WakeWordDetection:
        accepted = WakeWordRequest.model_validate(incoming)
        if accepted.operation_id in self.pending:
            raise WakeWordProviderError(WakeWordProviderErrorCode.BUSY)
        task = asyncio.current_task()
        assert task is not None
        self.pending[accepted.operation_id] = task
        self.entered.set()
        try:
            await self.release.wait()
            return detection(accepted, provider_id=self.provider_id)
        finally:
            del self.pending[accepted.operation_id]

    async def cancel(self, operation_id: str) -> CancellationResult:
        task = self.pending.get(operation_id)
        if task is not None:
            task.cancel()
        return CancellationResult(
            operation_id=operation_id,
            provider_id=self.provider_id,
            status=CancellationStatus.ACCEPTED
            if task
            else CancellationStatus.NOT_FOUND,
        )

    async def aclose(self) -> None:
        self.did_close = True


async def consume(
    provider: WakeWordProvider, incoming: WakeWordRequest
) -> WakeWordDetection:
    evidence = await provider.capabilities()
    evidence = WakeWordCapabilities.model_validate(evidence)
    evidence.validate_provider(provider.provider_id)
    value = await provider.detect(WakeWordRequest.model_validate(incoming))
    value = WakeWordDetection.model_validate(value)
    value.validate_request(incoming, provider.provider_id)
    return value


def cancellation_snapshot(
    value: object, provider_id: str, operation_id: str
) -> CancellationResult:
    if type(value) is not CancellationResult or value.__pydantic_extra__:
        raise WakeWordProviderError(WakeWordProviderErrorCode.INVALID_RESPONSE)
    try:
        accepted = CancellationResult.model_validate(dict(vars(value)))
    except ValidationError:
        raise WakeWordProviderError(
            WakeWordProviderErrorCode.INVALID_RESPONSE
        ) from None
    if (accepted.provider_id, accepted.operation_id) != (provider_id, operation_id):
        raise WakeWordProviderError(WakeWordProviderErrorCode.INVALID_RESPONSE)
    if not any(accepted.status is member for member in CancellationStatus):
        raise WakeWordProviderError(WakeWordProviderErrorCode.INVALID_RESPONSE)
    return accepted


def test_abc_requires_full_interface() -> None:
    assert WakeWordProvider.__abstractmethods__ == {
        "active_operation_ids",
        "capabilities",
        "detect",
        "cancel",
        "aclose",
    }
    with pytest.raises(TypeError):
        WakeWordProvider("abstract")  # type: ignore[abstract]
    for name in WakeWordProvider.__abstractmethods__:
        if name != "active_operation_ids":
            assert inspect.iscoroutinefunction(getattr(WakeWordProvider, name))
    for name in WakeWordProvider.__abstractmethods__:
        members = {
            member: ImmediateProvider.__dict__[member]
            for member in WakeWordProvider.__abstractmethods__
            if member != name
        }
        incomplete = type("Incomplete", (WakeWordProvider,), members)
        with pytest.raises(TypeError):
            incomplete("incomplete")


@pytest.mark.parametrize("value", BAD_IDS)
def test_provider_strict_id(value: object) -> None:
    fail(lambda: ImmediateProvider(value), "invalid_input")  # type: ignore[arg-type]


def test_provider_id_read_only_normalized_and_no_extra_configuration() -> None:
    provider = ImmediateProvider("  Provider-Case  ")
    assert (
        provider.provider_id == "Provider-Case" and provider.active_operation_ids == ()
    )
    with pytest.raises(AttributeError):
        provider.provider_id = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        ImmediateProvider("id", model_path="path")  # type: ignore[call-arg]


def test_two_independent_fakes_substitute_same_abc() -> None:
    async def exercise() -> None:
        providers: tuple[WakeWordProvider, ...] = (
            ImmediateProvider("Provider-A"),
            ControlledProvider("Provider-B"),
        )
        cast(ControlledProvider, providers[1]).release.set()
        incoming = request()
        original = incoming.model_dump_json()
        for provider in providers:
            evidence = await consume(provider, incoming)
            assert evidence.provider_id == provider.provider_id
            assert incoming.model_dump_json() == original
            assert provider.active_operation_ids == ()
            cancellation_snapshot(
                await provider.cancel("absent"), provider.provider_id, "absent"
            )
            await provider.aclose()
            assert getattr(provider, "did_close")
        assert not (await consume(providers[0], incoming)).detected

    run(exercise())


def test_scoped_cancellation_keeps_unrelated_operation_and_sorted_ids() -> None:
    async def exercise() -> None:
        provider = ControlledProvider("Provider-B")
        a = asyncio.create_task(consume(provider, request(operation_id="Z-A")))
        await asyncio.wait_for(provider.entered.wait(), 1)
        provider.entered.clear()
        b = asyncio.create_task(consume(provider, request(operation_id="A-B")))
        await asyncio.wait_for(provider.entered.wait(), 1)
        assert provider.active_operation_ids == ("A-B", "Z-A")
        fail_code = WakeWordProviderErrorCode.BUSY
        with pytest.raises(WakeWordProviderError) as caught:
            await provider.detect(request(operation_id="Z-A"))
        assert caught.value.code is fail_code
        accepted = cancellation_snapshot(
            await provider.cancel("Z-A"), "Provider-B", "Z-A"
        )
        assert accepted.status is CancellationStatus.ACCEPTED
        with pytest.raises(asyncio.CancelledError):
            await a
        assert provider.active_operation_ids == ("A-B",) and not b.done()
        absent = cancellation_snapshot(
            await provider.cancel("missing"), "Provider-B", "missing"
        )
        assert absent.status is CancellationStatus.NOT_FOUND
        provider.release.set()
        assert (await asyncio.wait_for(b, 1)).operation_id == "A-B"
        assert provider.active_operation_ids == ()
        assert (
            await consume(provider, request(operation_id="Z-A"))
        ).operation_id == "Z-A"
        await provider.aclose()
        assert provider.did_close

    run(exercise())


@pytest.mark.parametrize(
    "kind", ["cap-provider", "cap-forged", "detection-provider", "detection-forged"]
)
def test_consumer_revalidates_fake_adapter_evidence(kind: str) -> None:
    class CorruptProvider(ImmediateProvider):
        async def capabilities(self) -> WakeWordCapabilities:
            value = await super().capabilities()
            return (
                value.model_copy(update={"provider_id": "wrong"})
                if kind == "cap-provider"
                else (
                    value.model_copy(update={"supports_cancellation": "true"})
                    if kind == "cap-forged"
                    else value
                )
            )

        async def detect(self, incoming: WakeWordRequest) -> WakeWordDetection:
            value = await super().detect(incoming)
            return (
                value.model_copy(update={"provider_id": "wrong"})
                if kind == "detection-provider"
                else (
                    value.model_copy(update={"score": True})
                    if kind == "detection-forged"
                    else value
                )
            )

    with pytest.raises(WakeWordProviderError) as caught:
        run(consume(CorruptProvider("Provider-A"), request()))
    assert caught.value.code is WakeWordProviderErrorCode.INVALID_RESPONSE


@pytest.mark.parametrize(
    "changes",
    [
        {"provider_id": "wrong"},
        {"operation_id": "wrong"},
        {"status": "unknown"},
        {"approved": True},
    ],
)
def test_cancellation_exact_correlation(changes: dict[str, object]) -> None:
    value = CancellationResult(
        operation_id="operation",
        provider_id="provider",
        status=CancellationStatus.ACCEPTED,
    )
    fail(
        lambda: cancellation_snapshot(
            value.model_copy(update=changes), "provider", "operation"
        ),
        "invalid_response",
    )


def test_unsupported_phrase_is_explicit_and_no_retry_or_fallback() -> None:
    with pytest.raises(WakeWordProviderError) as caught:
        run(
            consume(
                ImmediateProvider("Provider-A"),
                request(phrase=phrase(text="Hey Phoenix")),
            )
        )
    assert caught.value.code is WakeWordProviderErrorCode.UNSUPPORTED_PHRASE


def forbidden(*args: object, **kwargs: object) -> Never:
    raise AssertionError("Unexpected external side effect")


def test_guarded_uncached_import_construction_validation_and_fake_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = [
        (name, compile((ROOT / path).read_text(encoding="utf-8"), path, "exec"))
        for name, path in (
            ("phoenix.voice", "phoenix/voice/__init__.py"),
            ("phoenix.voice.audio", "phoenix/voice/audio.py"),
            ("phoenix.voice.wake", "phoenix/voice/wake.py"),
        )
    ]

    async def exercise() -> None:
        with monkeypatch.context() as scoped:
            for target, name in (
                (builtins, "open"),
                (Path, "open"),
                (Path, "read_text"),
                (Path, "write_text"),
                (os, "open"),
                (os, "mkdir"),
                (os, "makedirs"),
                (sqlite3, "connect"),
                (socket, "socket"),
                (socket, "getaddrinfo"),
                (socket, "create_connection"),
                (subprocess, "Popen"),
                (threading.Thread, "start"),
                (asyncio, "create_task"),
                (asyncio, "ensure_future"),
                (logging.Logger, "_log"),
                (builtins, "print"),
                (EventBus, "publish"),
                (EventBus, "subscribe"),
            ):
                scoped.setattr(target, name, forbidden)
            if hasattr(ctypes, "WinDLL"):
                scoped.setattr(ctypes, "WinDLL", forbidden)
            scoped.setattr(asyncio.get_running_loop(), "create_task", forbidden)

            def environment(mapping: object, key: str) -> str:
                if key == "PYDANTIC_DISABLE_PLUGINS":
                    raise KeyError(key)
                return forbidden()

            def getenv(key: str, default: object = None) -> object:
                if key == "PYDANTIC_DISABLE_PLUGINS":
                    return default
                return forbidden()

            scoped.setattr(type(os.environ), "__getitem__", environment)
            scoped.setattr(os, "getenv", getenv)
            importer = builtins.__import__

            def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
                if name.startswith("phoenix.") and name not in (
                    "phoenix.voice.audio",
                    "phoenix.models.provider",
                ):
                    return forbidden()
                return importer(name, *args, **kwargs)

            scoped.setattr(builtins, "__import__", guarded_import)
            loaded: dict[str, Any] = {}
            for name, code in sources:
                spec = importlib.util.spec_from_loader(name, loader=None)
                assert spec is not None
                module = importlib.util.module_from_spec(spec)
                scoped.setitem(sys.modules, name, module)
                exec(code, module.__dict__)
                loaded[name] = module
            a = loaded["phoenix.voice.audio"]
            w = loaded["phoenix.voice.wake"]
            f = a.PcmAudioFrame.model_validate(frame_values())
            incoming = w.WakeWordRequest(
                operation_id="op", phrase=w.DEFAULT_WAKE_PHRASE, frames=(f,)
            )
            generated = w.WakeWordRequest(phrase=w.DEFAULT_WAKE_PHRASE, frames=(f,))
            assert UUID(generated.operation_id).version == 4
            assert f.pcm_bytes_for_provider() == PCM
            w.WakeWordRequest.model_validate_json(incoming.model_dump_json())

            async def describe(self: Any) -> Any:
                return w.WakeWordCapabilities(
                    provider_id=self.provider_id,
                    supported_phrases=(w.DEFAULT_WAKE_PHRASE,),
                    sample_rate_hz=16000,
                    channels=1,
                    sample_width_bytes=2,
                    encoding="pcm_s16le",
                    supports_cancellation=False,
                )

            async def detect(self: Any, delivered: Any) -> Any:
                return w.WakeWordDetection(
                    operation_id=delivered.operation_id,
                    provider_id=self.provider_id,
                    phrase_id=delivered.phrase.phrase_id,
                    stream_id=delivered.frames[0].stream_id,
                    start_sequence=0,
                    end_sequence=0,
                    detected=True,
                    score=1.0,
                )

            async def cancel(self: Any, operation_id: str) -> CancellationResult:
                return CancellationResult(
                    operation_id=operation_id,
                    provider_id=self.provider_id,
                    status=CancellationStatus.NOT_FOUND,
                )

            async def close(self: Any) -> None:
                return None

            provider = type(
                "IsolatedFake",
                (w.WakeWordProvider,),
                {
                    "active_operation_ids": property(lambda self: ()),
                    "capabilities": describe,
                    "detect": detect,
                    "cancel": cancel,
                    "aclose": close,
                },
            )("isolated")
            (await provider.capabilities()).validate_provider(provider.provider_id)
            result = await provider.detect(incoming)
            result.validate_request(incoming, provider.provider_id)
            assert result.detected and provider.active_operation_ids == ()
            cancellation_snapshot(await provider.cancel("op"), "isolated", "op")
            await provider.aclose()

    run(exercise())


def frame_values() -> dict[str, object]:
    # Avoid invoking the original frame class while its module is being isolated.
    return dict(
        stream_id="stream",
        sequence=0,
        captured_at=NOW,
        sample_rate_hz=16000,
        channels=1,
        sample_width_bytes=2,
        encoding="pcm_s16le",
        pcm_base64=base64.b64encode(PCM).decode(),
        pcm_sha256=sha256(PCM).hexdigest(),
    )


@pytest.mark.parametrize(
    "path",
    ["phoenix/voice/__init__.py", "phoenix/voice/audio.py", "phoenix/voice/wake.py"],
)
def test_static_separation_no_device_runtime_logging_or_execution(path: str) -> None:
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    allowed = {
        "__future__",
        "abc",
        "base64",
        "binascii",
        "collections.abc",
        "datetime",
        "enum",
        "hashlib",
        "math",
        "typing",
        "uuid",
        "pydantic",
        "phoenix.voice.audio",
        "phoenix.models.provider",
    }
    banned_calls = {
        "eval",
        "exec",
        "open",
        "print",
        "publish",
        "subscribe",
        "create_task",
        "ensure_future",
        "to_thread",
        "run_in_executor",
        "execute",
        "system",
        "getenv",
        "WinDLL",
        "connect",
        "socket",
        "getaddrinfo",
        "Popen",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name in allowed for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module in allowed
            if node.module == "phoenix.models.provider":
                assert [alias.name for alias in node.names] == ["CancellationResult"]
        elif isinstance(node, ast.Call):
            name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else ""
            )
            assert name not in banned_calls
        elif isinstance(node, ast.ClassDef):
            assert not node.name.startswith(("Fake", "Mock"))
    paths = {p.name for p in (ROOT / "phoenix/voice").glob("*.py")}
    assert paths == {"__init__.py", "audio.py", "wake.py"}


def test_prior_production_manifests_migrations_frontend_events_unchanged() -> None:
    changed = subprocess.check_output(
        [
            "git",
            "diff",
            "--name-only",
            BASELINE,
            "--",
            "phoenix/agents",
            "phoenix/models",
            "phoenix/tools",
            "phoenix/security",
            "phoenix/web",
            "phoenix/memory",
            "phoenix/rag",
            "phoenix/persistence",
            "phoenix/core",
            "phoenix/api",
            "alembic",
            "apps",
            "pyproject.toml",
            "uv.lock",
        ],
        cwd=ROOT,
        text=True,
    )
    assert changed == ""
    assert len(EventType) == 32
    assert ScriptDirectory.from_config(
        Config(str(ROOT / "alembic.ini"))
    ).get_heads() == ["0005_audit_events"]


def test_repository_credential_signature_scan() -> None:
    paths = subprocess.check_output(
        ["git", "ls-files"], cwd=ROOT, text=True
    ).splitlines()
    paths.extend(
        [
            "phoenix/voice/__init__.py",
            "phoenix/voice/audio.py",
            "phoenix/voice/wake.py",
            "tests/test_wake_word_provider.py",
        ]
    )
    signatures = [
        re.compile(pattern)
        for pattern in (
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE" + r" KEY-----",
            r"github_" + r"pat_[A-Za-z0-9_]{20,}",
            r"gh" + r"p_[A-Za-z0-9]{30,}",
            r"xox" + r"[baprs]-[A-Za-z0-9-]{20,}",
            r"AK" + r"IA[0-9A-Z]{16}",
        )
    ]
    for path in set(paths):
        text = (ROOT / path).read_bytes().decode("utf-8", errors="replace")
        assert not any(pattern.search(text) for pattern in signatures), path
