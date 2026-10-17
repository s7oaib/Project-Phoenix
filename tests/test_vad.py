"""PHX-061 synthetic frames, exact contracts, energy math and lifecycle proof."""

from __future__ import annotations

import ast
import asyncio
import base64
import builtins
import ctypes
import importlib.util
import inspect
import logging
import math
import os
import re
import socket
import sqlite3
import struct
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
from phoenix.voice import vad
from phoenix.voice.audio import PcmAudioFrame
from phoenix.voice.vad import (
    DEFAULT_ENERGY_VAD_THRESHOLD_DBFS,
    EnergyVadProvider,
    VadActivity,
    VadCapabilities,
    VadDecision,
    VadFrameDuration,
    VadProvider,
    VadProviderError,
    VadProviderErrorCode,
    VadRequest,
)
from phoenix.voice.wake import DEFAULT_WAKE_PHRASE

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "4cc0feb59ae91bc77b72c7a3a0f1a7885f397ee3"
NOW = datetime(2026, 10, 7, 12, tzinfo=UTC)
BAD = (None, True, 1, 1.5, b"value", object(), "\0", "\ud800")
BAD_IDS = BAD + ("", " ", "x\n", "\tx", "x\x7f", "x\x85", "x" * 257)
T = TypeVar("T")


def run(value: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(value)


def frame(size: int = 320, /, *, amplitude: int = 0, **changes: Any) -> PcmAudioFrame:
    data = struct.pack("<h", amplitude) * (size // 2)
    return pcm_frame(data, **changes)


def pcm_frame(data: bytes, /, **changes: Any) -> PcmAudioFrame:
    return PcmAudioFrame.model_validate(
        dict(
            stream_id="Stream-A",
            sequence=0,
            captured_at=NOW,
            sample_rate_hz=16000,
            channels=1,
            sample_width_bytes=2,
            encoding="pcm_s16le",
            pcm_base64=base64.b64encode(data).decode(),
            pcm_sha256=sha256(data).hexdigest(),
        )
        | changes
    )


def capabilities(**changes: Any) -> VadCapabilities:
    return VadCapabilities.model_validate(
        dict(
            provider_id="Provider-A",
            supported_durations=tuple(VadFrameDuration),
            sample_rate_hz=16000,
            channels=1,
            sample_width_bytes=2,
            encoding="pcm_s16le",
            supports_cancellation=True,
        )
        | changes
    )


def request(**changes: Any) -> VadRequest:
    return VadRequest.model_validate(
        dict(operation_id="Operation-A", frame=frame(), duration=VadFrameDuration.MS_10)
        | changes
    )


def decision(incoming: VadRequest | None = None, /, **changes: Any) -> VadDecision:
    incoming = incoming or request()
    return VadDecision.model_validate(
        dict(
            operation_id=incoming.operation_id,
            provider_id="Provider-A",
            stream_id=incoming.frame.stream_id,
            sequence=incoming.frame.sequence,
            captured_at=incoming.frame.captured_at,
            activity=VadActivity.SPEECH,
            score=0.25,
            duration=incoming.duration,
        )
        | changes
    )


def fail(call: Callable[[], object], code: str | None = None) -> None:
    with pytest.raises(VadProviderError) as caught:
        call()
    assert str(caught.value) == caught.value.code.value
    if code is not None:
        assert caught.value.code.value == code


def test_exact_vocabularies_mapping_default_and_safe_errors() -> None:
    assert [v.value for v in VadActivity] == ["silence", "speech"]
    assert [v.value for v in VadFrameDuration] == ["ms_10", "ms_20", "ms_30"]
    assert [v.pcm_byte_count for v in VadFrameDuration] == [320, 640, 960]
    assert [v.value for v in VadProviderErrorCode] == [
        "invalid_input",
        "invalid_response",
        "unsupported_audio",
        "unavailable",
        "timeout",
        "busy",
        "closed",
        "wrong_event_loop",
        "cleanup_failure",
    ]
    assert DEFAULT_ENERGY_VAD_THRESHOLD_DBFS == -42.0
    assert DEFAULT_WAKE_PHRASE.phrase_id == "rise_phoenix"
    assert DEFAULT_WAKE_PHRASE.text == "Rise, Phoenix"
    for code in VadProviderErrorCode:
        error = VadProviderError(code)
        assert error.args == (code.value,) and str(error) == code.value


class Foreign(StrEnum):
    ACTIVITY = "speech"
    DURATION = "ms_10"
    CODE = "invalid_input"


@pytest.mark.parametrize(
    "field,foreign", [("activity", Foreign.ACTIVITY), ("duration", Foreign.DURATION)]
)
def test_foreign_and_copied_enums(field: str, foreign: object) -> None:
    fail(lambda: decision(**{field: foreign}))
    cls = VadActivity if field == "activity" else VadFrameDuration
    member = VadActivity.SPEECH if field == "activity" else VadFrameDuration.MS_10
    copied = str.__new__(cls, member.value)
    copied._name_ = member.name
    copied._value_ = member.value
    fail(lambda: decision(**{field: copied}))
    if field == "duration":
        fail(lambda: request(duration=copied))
        fail(lambda: capabilities(supported_durations=(copied,)))


@pytest.mark.parametrize("code", ["invalid_input", Foreign.CODE, None, True, object()])
def test_invalid_error_codes(code: object) -> None:
    with pytest.raises(ValueError, match="^invalid_input$"):
        VadProviderError(code)  # type: ignore[arg-type]


FACTORIES: tuple[Callable[..., BaseModel], ...] = (capabilities, request, decision)
FIELDS = (
    "provider_id supported_durations sample_rate_hz channels sample_width_bytes "
    "encoding supports_cancellation",
    "operation_id frame duration",
    "operation_id provider_id stream_id sequence captured_at activity score duration",
)


@pytest.mark.parametrize("factory,fields", zip(FACTORIES, FIELDS))
def test_contract_exact_fields_frozen_snapshot_and_json(
    factory: Callable[..., BaseModel], fields: str
) -> None:
    value = factory()
    assert set(type(value).model_fields) == set(fields.split())
    assert type(value).model_validate_json(value.model_dump_json()) == value
    assert type(value).model_validate(value) is not value
    with pytest.raises(ValidationError):
        setattr(value, fields.split()[0], "altered")
    fail(lambda: type(value).model_validate(value.model_dump() | {"approved": True}))


@pytest.mark.parametrize(
    "factory,field",
    [
        (capabilities, "provider_id"),
        (request, "operation_id"),
        (decision, "operation_id"),
        (decision, "provider_id"),
        (decision, "stream_id"),
    ],
)
@pytest.mark.parametrize("value", BAD_IDS)
def test_strict_ids(
    factory: Callable[..., BaseModel], field: str, value: object
) -> None:
    fail(lambda: factory(**{field: value}))


@pytest.mark.parametrize(
    "factory,field",
    [
        (capabilities, "provider_id"),
        (request, "operation_id"),
        (decision, "operation_id"),
        (decision, "provider_id"),
        (decision, "stream_id"),
    ],
)
def test_id_normalization_case_unicode_and_bound(
    factory: Callable[..., BaseModel], field: str
) -> None:
    assert (
        getattr(factory(**{field: "  Case-Ελληνικά 🌐  "}), field) == "Case-Ελληνικά 🌐"
    )
    assert getattr(factory(**{field: "x" * 256}), field) == "x" * 256
    fail(lambda: factory(**{field: "x" * 257}))


@pytest.mark.parametrize(
    "durations",
    [
        (VadFrameDuration.MS_10,),
        (VadFrameDuration.MS_20,),
        (VadFrameDuration.MS_30,),
        (VadFrameDuration.MS_10, VadFrameDuration.MS_30),
        tuple(VadFrameDuration),
    ],
)
def test_supported_duration_subsets_detached_canonical_order(
    durations: tuple[VadFrameDuration, ...],
) -> None:
    supplied = list(durations)
    value = capabilities(supported_durations=supplied)
    supplied.clear()
    assert (
        value.supported_durations == durations
        and type(value.supported_durations) is tuple
    )


@pytest.mark.parametrize(
    "value",
    [
        (),
        [],
        None,
        {},
        "ms_10",
        {"ms_10"},
        ("ms_10", "ms_10"),
        ("ms_20", "ms_10"),
        ("ms_30", "ms_20", "ms_10"),
        ("ms_10",) * 4,
        (Foreign.DURATION,),
    ],
)
def test_bad_supported_durations(value: object) -> None:
    fail(lambda: capabilities(supported_durations=value), "invalid_response")


@pytest.mark.parametrize(
    "field,expected",
    [("sample_rate_hz", 16000), ("channels", 1), ("sample_width_bytes", 2)],
)
@pytest.mark.parametrize("variant", ["float", "string", "bool", "wrong", "none"])
def test_capability_format_strict(field: str, expected: int, variant: str) -> None:
    value = {
        "float": float(expected),
        "string": str(expected),
        "bool": True,
        "wrong": expected + 1,
        "none": None,
    }[variant]
    fail(lambda: capabilities(**{field: value}), "invalid_response")


@pytest.mark.parametrize(
    "value", ["wav", "pcm_f32le", "PCM_S16LE", b"pcm_s16le", None, True]
)
def test_capability_encoding_exact(value: object) -> None:
    fail(lambda: capabilities(encoding=value))


@pytest.mark.parametrize("value", [0, 1, "true", None, 0.0])
def test_capability_cancellation_strict_bool(value: object) -> None:
    fail(lambda: capabilities(supports_cancellation=value))


def test_capability_provider_helper_revalidates() -> None:
    capabilities().validate_provider(" Provider-A ")
    fail(lambda: capabilities().validate_provider("other"), "invalid_response")
    fail(
        lambda: capabilities()
        .model_copy(update={"supports_cancellation": "true"})
        .validate_provider("Provider-A")
    )
    fail(lambda: capabilities().validate_provider(True), "invalid_input")  # type: ignore[arg-type]


@pytest.mark.parametrize("duration", list(VadFrameDuration))
def test_exact_duration_byte_admission_and_private_detached_frame(
    duration: VadFrameDuration,
) -> None:
    supplied = frame(duration.pcm_byte_count)
    value = request(frame=supplied, duration=duration)
    assert value.frame is not supplied and value.frame == supplied
    assert len(value.frame.pcm_bytes_for_provider()) == duration.pcm_byte_count
    assert "pcm_base64" not in repr(value) and supplied.pcm_base64 not in str(value)
    supplied.__dict__["sequence"] = 99
    assert value.frame.sequence == 0
    first = VadRequest(frame=value.frame, duration=duration)
    second = VadRequest(frame=value.frame, duration=duration)
    assert (
        UUID(first.operation_id).version == 4
        and first.operation_id != second.operation_id
    )


@pytest.mark.parametrize("duration", list(VadFrameDuration))
@pytest.mark.parametrize("difference", [-2, 2, 32000])
def test_wrong_byte_length_unsupported_no_repair(
    duration: VadFrameDuration, difference: int
) -> None:
    size = duration.pcm_byte_count + difference if difference != 32000 else 32000
    supplied = frame(size)
    original = supplied.model_dump_json()
    fail(lambda: request(frame=supplied, duration=duration), "unsupported_audio")
    assert supplied.model_dump_json() == original


@pytest.mark.parametrize("factory", [request, decision])
@pytest.mark.parametrize(
    "value", ["ms_40", "10", 10, True, b"ms_10", Foreign.DURATION, None]
)
def test_duration_exact_enum(factory: Callable[..., BaseModel], value: object) -> None:
    fail(lambda: factory(duration=value))


@pytest.mark.parametrize("value", [-1, True, False, "0", 0.0, None])
def test_decision_sequence_strict(value: object) -> None:
    fail(lambda: decision(sequence=value))


def test_decision_zero_sequence_and_utc_explicit() -> None:
    local = NOW.astimezone(timezone(timedelta(hours=5, minutes=30)))
    value = decision(sequence=0, captured_at=local)
    assert (
        value.sequence == 0
        and value.captured_at == NOW
        and value.captured_at.tzinfo is UTC
    )
    assert decision(captured_at=local.isoformat()).captured_at == NOW
    data = value.model_dump()
    del data["captured_at"]
    fail(lambda: VadDecision.model_validate(data))


@pytest.mark.parametrize(
    "value", [datetime(2026, 1, 1), "2026-01-01T00:00:00", 0, True, None, "bad"]
)
def test_decision_invalid_timestamp(value: object) -> None:
    fail(lambda: decision(captured_at=value))


@pytest.mark.parametrize(
    "value", ["noise", "SPEECH", b"speech", None, True, Foreign.ACTIVITY]
)
def test_activity_exact(value: object) -> None:
    fail(lambda: decision(activity=value))


@pytest.mark.parametrize(
    "value",
    [
        0,
        1,
        True,
        False,
        "0.5",
        None,
        float("nan"),
        float("inf"),
        -float("inf"),
        -0.01,
        1.01,
    ],
)
def test_score_ordinary_finite_float(value: object) -> None:
    fail(lambda: decision(score=value))


@pytest.mark.parametrize("activity", list(VadActivity))
@pytest.mark.parametrize("score", [0.0, 0.5, 1.0])
def test_neutral_score_never_overrides_activity(
    activity: VadActivity, score: float
) -> None:
    value = decision(activity=activity, score=score)
    value.validate_request(request(), "Provider-A")
    assert value.activity is activity and value.score == score


@pytest.mark.parametrize(
    "changes",
    [
        {"operation_id": "other"},
        {"provider_id": "other"},
        {"stream_id": "other"},
        {"sequence": 1},
        {"captured_at": NOW + timedelta(microseconds=1)},
        {"duration": VadFrameDuration.MS_20},
    ],
)
def test_all_six_correlation_mismatches(changes: dict[str, object]) -> None:
    fail(
        lambda: decision(**changes).validate_request(request(), "Provider-A"),
        "invalid_response",
    )


@pytest.mark.parametrize("factory", FACTORIES)
@pytest.mark.parametrize(
    "attack",
    [
        "subclass",
        "extra",
        "collision",
        "extra-type",
        "missing-extra",
        "copy-extra",
        "missing-field",
        "invalid-field",
    ],
)
def test_stored_value_forgery_rejection(
    factory: Callable[..., BaseModel], attack: str
) -> None:
    value = factory()
    cls = type(value)
    field = next(iter(cls.model_fields))
    if attack == "subclass":
        value = cast(type[BaseModel], type("Extended", (cls,), {})).model_construct(
            **vars(value)
        )
    elif attack in ("extra", "collision", "extra-type"):
        object.__setattr__(
            value,
            "__pydantic_extra__",
            {field: "forged"}
            if attack == "collision"
            else {"approved": True}
            if attack == "extra"
            else [],
        )
    elif attack == "missing-extra":
        object.__delattr__(value, "__pydantic_extra__")
    elif attack == "copy-extra":
        value = value.model_copy(update={"approved": True})
    elif attack == "missing-field":
        del vars(value)[field]
    else:
        vars(value)[field] = True
    fail(lambda: cls.model_validate(value))


@pytest.mark.parametrize(
    "attack", ["sequence", "hash", "extra", "subclass", "collision"]
)
def test_frame_snapshot_attacks(attack: str) -> None:
    value = frame()
    if attack == "sequence":
        value = value.model_copy(update={"sequence": True})
    elif attack == "hash":
        value = value.model_copy(update={"pcm_sha256": "a" * 64})
    elif attack == "extra":
        value = value.model_copy(update={"approved": True})
    elif attack == "subclass":
        value = cast(
            type[PcmAudioFrame], type("Extended", (PcmAudioFrame,), {})
        ).model_construct(**vars(value))
    else:
        object.__setattr__(value, "__pydantic_extra__", {"sequence": 9})
    fail(lambda: request(frame=value), "invalid_input")


@pytest.mark.parametrize("factory", FACTORIES)
@pytest.mark.parametrize(
    "field",
    [
        "threshold",
        "threshold_dbfs",
        "model_path",
        "device_id",
        "microphone_handle",
        "api_key",
        "callback",
        "tool_id",
        "permission",
        "risk",
        "approval",
        "transcript",
        "language",
        "user_identity",
        "speaker_identity",
        "action",
        "conversation_state",
        "barge_in",
        "startup_health",
        "wake_phrase",
    ],
)
def test_no_authority_text_or_adapter_options(
    factory: Callable[..., BaseModel], field: str
) -> None:
    fail(lambda: type(factory()).model_validate(factory().model_dump() | {field: True}))


@pytest.mark.parametrize("threshold", [-90.0, -42.0, -10.0])
def test_energy_constructor_threshold_endpoints_and_readonly(threshold: float) -> None:
    provider = EnergyVadProvider(threshold_dbfs=threshold)
    assert (
        provider.threshold_dbfs == threshold and provider.provider_id == "energy_vad_v1"
    )
    assert (
        not provider.closed
        and provider.active_operation_ids == ()
        and provider._loop is None
    )
    assert (
        EnergyVadProvider("  Custom-ID  ", threshold_dbfs=threshold).provider_id
        == "Custom-ID"
    )
    with pytest.raises(AttributeError):
        provider.threshold_dbfs = -50.0  # type: ignore[misc]
    with pytest.raises(AttributeError):
        provider.provider_id = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    "value",
    [
        -90.01,
        -9.99,
        True,
        False,
        -42,
        "-42",
        None,
        float("nan"),
        float("inf"),
        -float("inf"),
    ],
)
def test_invalid_energy_threshold(value: object) -> None:
    fail(lambda: EnergyVadProvider(threshold_dbfs=value), "invalid_input")  # type: ignore[arg-type]


@pytest.mark.parametrize("value", BAD_IDS)
def test_invalid_energy_provider_identity(value: object) -> None:
    fail(lambda: EnergyVadProvider(value), "invalid_input")  # type: ignore[arg-type]


@pytest.mark.parametrize("duration", list(VadFrameDuration))
@pytest.mark.parametrize("amplitude", [0, 1, 100, 1024, 32767, -32768, -1024, -100])
def test_constant_signed_energy_math(
    duration: VadFrameDuration, amplitude: int
) -> None:
    async def exercise() -> None:
        provider = EnergyVadProvider()
        incoming = request(
            frame=frame(duration.pcm_byte_count, amplitude=amplitude), duration=duration
        )
        original = incoming.model_dump_json()
        value = await provider.classify(incoming)
        value.validate_request(incoming, "energy_vad_v1")
        dbfs = (
            -96.0
            if amplitude == 0
            else max(-96.0, min(0.0, 20 * math.log10(abs(amplitude) / 32768.0)))
        )
        assert value.activity is (
            VadActivity.SPEECH if dbfs >= -42.0 else VadActivity.SILENCE
        )
        assert value.score == pytest.approx((dbfs + 96.0) / 96.0, abs=1e-15)
        assert 0.0 <= value.score <= 1.0 and math.isfinite(value.score)
        if amplitude == 0:
            assert value.activity is VadActivity.SILENCE and value.score == 0.0
        if amplitude == -32768:
            assert value.score == 1.0
        for _ in range(3):
            assert await provider.classify(incoming) == value
        assert (
            incoming.model_dump_json() == original
            and provider.active_operation_ids == ()
        )
        assert set(vars(provider)) == {
            "_provider_id",
            "_threshold_dbfs",
            "_loop",
            "_closed",
        }
        await provider.aclose()

    run(exercise())


@pytest.mark.parametrize("duration", list(VadFrameDuration))
@pytest.mark.parametrize("threshold_offset", [0, -1, 1])
def test_exact_threshold_and_adjacent_float_boundaries(
    duration: VadFrameDuration, threshold_offset: int
) -> None:
    async def exercise() -> None:
        exact = 20 * math.log10(1024 / 32768.0)
        threshold = (
            exact
            if threshold_offset == 0
            else math.nextafter(exact, math.inf if threshold_offset > 0 else -math.inf)
        )
        provider = EnergyVadProvider(threshold_dbfs=threshold)
        incoming = request(
            frame=frame(duration.pcm_byte_count, amplitude=1024), duration=duration
        )
        value = await provider.classify(incoming)
        assert value.activity is (
            VadActivity.SILENCE if threshold_offset > 0 else VadActivity.SPEECH
        )
        below = request(
            frame=frame(duration.pcm_byte_count, amplitude=1023), duration=duration
        )
        assert (await provider.classify(below)).activity is VadActivity.SILENCE

    run(exercise())


@pytest.mark.parametrize("duration", list(VadFrameDuration))
def test_alternating_and_nonuniform_samples_exact_rms(
    duration: VadFrameDuration,
) -> None:
    async def exercise() -> None:
        provider = EnergyVadProvider()
        count = duration.pcm_byte_count // 2
        alternating = struct.pack("<hh", 2048, -2048) * (count // 2)
        alternate_result = await provider.classify(
            request(frame=pcm_frame(alternating), duration=duration)
        )
        constant_result = await provider.classify(
            request(
                frame=frame(duration.pcm_byte_count, amplitude=2048), duration=duration
            )
        )
        assert alternate_result == constant_result
        nonuniform = struct.pack("<h", -32768) + b"\0\0" * (count - 1)
        expected_rms = math.sqrt(32768**2 / count)
        expected_dbfs = 20 * math.log10(expected_rms / 32768.0)
        result = await provider.classify(
            request(frame=pcm_frame(nonuniform), duration=duration)
        )
        assert result.score == pytest.approx((expected_dbfs + 96.0) / 96.0, abs=1e-15)

    run(exercise())


def test_energy_capabilities_cancel_close_and_closed_methods() -> None:
    async def exercise() -> None:
        provider = EnergyVadProvider()
        value = await provider.capabilities()
        value.validate_provider("energy_vad_v1")
        assert (
            value.supported_durations == tuple(VadFrameDuration)
            and not value.supports_cancellation
        )
        cancellation = await provider.cancel(" Operation-A ")
        assert (
            cancellation.operation_id == "Operation-A"
            and cancellation.provider_id == provider.provider_id
        )
        assert (
            cancellation.status is CancellationStatus.UNSUPPORTED
            and provider.active_operation_ids == ()
        )
        await provider.aclose()
        await provider.aclose()
        assert provider.closed
        for method in (
            provider.capabilities(),
            provider.classify(request()),
            provider.cancel("id"),
        ):
            with pytest.raises(VadProviderError) as caught:
                await method
            assert caught.value.code is VadProviderErrorCode.CLOSED

    run(exercise())


def test_energy_cannot_reinitialize_or_reopen() -> None:
    async def exercise() -> None:
        provider = EnergyVadProvider()
        await provider.capabilities()
        owner = provider._loop
        fail(lambda: EnergyVadProvider.__init__(provider, "different"), "invalid_input")
        assert provider.provider_id == "energy_vad_v1" and provider._loop is owner
        await provider.aclose()
        fail(lambda: EnergyVadProvider.__init__(provider), "closed")
        assert provider.closed and provider._loop is owner

    run(exercise())


async def invoke(provider: EnergyVadProvider, method: str) -> object:
    if method == "classify":
        return await provider.classify(request())
    if method == "cancel":
        return await provider.cancel("operation")
    if method == "aclose":
        await provider.aclose()
        return None
    return await provider.capabilities()


@pytest.mark.parametrize("first", ["capabilities", "classify", "cancel", "aclose"])
@pytest.mark.parametrize("second", ["capabilities", "classify", "cancel", "aclose"])
def test_all_async_methods_loop_binding_and_cross_loop_rejection(
    first: str, second: str
) -> None:
    provider = EnergyVadProvider()
    assert provider._loop is None
    run(invoke(provider, first))
    owner = provider._loop
    with pytest.raises(VadProviderError) as caught:
        run(invoke(provider, second))
    assert caught.value.code is VadProviderErrorCode.WRONG_EVENT_LOOP
    assert provider._loop is owner


def test_energy_classify_exact_request_and_error_privacy(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def exercise() -> None:
        provider = EnergyVadProvider()
        bad = request().model_copy(update={"frame": frame(322)})
        with pytest.raises(VadProviderError) as caught:
            await provider.classify(bad)
        assert caught.value.code is VadProviderErrorCode.UNSUPPORTED_AUDIO
        for invalid in (None, request().model_copy(update={"approved": True})):
            with pytest.raises(VadProviderError) as caught:
                await provider.classify(invalid)  # type: ignore[arg-type]
            assert caught.value.code is VadProviderErrorCode.INVALID_INPUT
        await provider.classify(request())
        fail(
            lambda: VadRequest.model_validate(
                request().model_dump() | {"PRIVATE-AUDIO": "sensitive"}
            )
        )
        assert caplog.records == []
        assert provider.active_operation_ids == ()

    run(exercise())
    private_payload = {"PRIVATE-AUDIO": "sensitive"}
    for call in (
        lambda: request(
            frame=frame().model_copy(update={"pcm_base64": "PRIVATE-AUDIO"})
        ),
        lambda: decision(**private_payload),
    ):
        with pytest.raises(VadProviderError) as caught:
            call()
        formatted = "".join(traceback.format_exception(caught.value))
        assert "PRIVATE-AUDIO" not in str(caught.value) and "sensitive" not in formatted


class FakeA(VadProvider):
    @property
    def active_operation_ids(self) -> tuple[str, ...]:
        return ()

    async def capabilities(self) -> VadCapabilities:
        return capabilities(provider_id=self.provider_id)

    async def classify(self, incoming: VadRequest) -> VadDecision:
        return decision(
            VadRequest.model_validate(incoming), provider_id=self.provider_id
        )

    async def cancel(self, operation_id: str) -> CancellationResult:
        return CancellationResult(
            operation_id=operation_id,
            provider_id=self.provider_id,
            status=CancellationStatus.ACCEPTED,
        )

    async def aclose(self) -> None:
        self.did_close = True


class FakeB(VadProvider):
    @property
    def active_operation_ids(self) -> tuple[str, ...]:
        return ()

    async def capabilities(self) -> VadCapabilities:
        return capabilities(provider_id=self.provider_id, supports_cancellation=False)

    async def classify(self, incoming: VadRequest) -> VadDecision:
        return decision(
            VadRequest.model_validate(incoming),
            provider_id=self.provider_id,
            activity=VadActivity.SILENCE,
            score=1.0,
        )

    async def cancel(self, operation_id: str) -> CancellationResult:
        return CancellationResult(
            operation_id=operation_id,
            provider_id=self.provider_id,
            status=CancellationStatus.NOT_FOUND,
        )

    async def aclose(self) -> None:
        self.did_close = True


async def consume(provider: VadProvider, incoming: VadRequest) -> VadDecision:
    info = VadCapabilities.model_validate(await provider.capabilities())
    info.validate_provider(provider.provider_id)
    value = VadDecision.model_validate(await provider.classify(incoming))
    value.validate_request(incoming, provider.provider_id)
    return value


def cancel_snapshot(
    value: object, provider_id: str, operation_id: str
) -> CancellationResult:
    if type(value) is not CancellationResult or value.__pydantic_extra__:
        raise VadProviderError(VadProviderErrorCode.INVALID_RESPONSE)
    try:
        result = CancellationResult.model_validate(dict(vars(value)))
    except ValidationError:
        raise VadProviderError(VadProviderErrorCode.INVALID_RESPONSE) from None
    if (result.provider_id, result.operation_id) != (provider_id, operation_id):
        raise VadProviderError(VadProviderErrorCode.INVALID_RESPONSE)
    return result


def test_abc_full_members_and_two_substitutable_fakes() -> None:
    members = {"active_operation_ids", "capabilities", "classify", "cancel", "aclose"}
    assert VadProvider.__abstractmethods__ == members
    with pytest.raises(TypeError):
        VadProvider("abstract")  # type: ignore[abstract]
    for missing in members:
        incomplete = type(
            "Incomplete",
            (VadProvider,),
            {key: FakeA.__dict__[key] for key in members if key != missing},
        )
        with pytest.raises(TypeError):
            incomplete("incomplete")
        if missing != "active_operation_ids":
            assert inspect.iscoroutinefunction(getattr(VadProvider, missing))

    async def exercise() -> None:
        providers: tuple[VadProvider, ...] = (
            FakeA(" A "),
            FakeB(" B "),
            EnergyVadProvider(),
        )
        for provider in providers:
            await consume(provider, request())
            assert (
                provider.active_operation_ids == ()
                and type(provider.active_operation_ids) is tuple
            )
            response = cancel_snapshot(
                await provider.cancel("operation"), provider.provider_id, "operation"
            )
            assert response.status in tuple(CancellationStatus)
            await provider.aclose()

    run(exercise())


@pytest.mark.parametrize("value", BAD_IDS)
def test_base_provider_strict_identity(value: object) -> None:
    fail(lambda: FakeA(value), "invalid_input")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kind", ["cap-provider", "cap-forged", "decision-provider", "decision-forged"]
)
def test_consumer_exact_revalidation(kind: str) -> None:
    class Corrupt(FakeA):
        async def capabilities(self) -> VadCapabilities:
            value = await super().capabilities()
            return (
                value.model_copy(update={"provider_id": "wrong"})
                if kind == "cap-provider"
                else value.model_copy(update={"supports_cancellation": "true"})
                if kind == "cap-forged"
                else value
            )

        async def classify(self, incoming: VadRequest) -> VadDecision:
            value = await super().classify(incoming)
            return (
                value.model_copy(update={"provider_id": "wrong"})
                if kind == "decision-provider"
                else value.model_copy(update={"score": True})
                if kind == "decision-forged"
                else value
            )

    with pytest.raises(VadProviderError) as caught:
        run(consume(Corrupt("provider"), request()))
    assert caught.value.code is VadProviderErrorCode.INVALID_RESPONSE


@pytest.mark.parametrize(
    "changes",
    [
        {"provider_id": "wrong"},
        {"operation_id": "wrong"},
        {"status": "wrong"},
        {"approved": True},
    ],
)
def test_cancellation_correlation(changes: dict[str, object]) -> None:
    value = CancellationResult(
        provider_id="provider",
        operation_id="operation",
        status=CancellationStatus.ACCEPTED,
    )
    fail(
        lambda: cancel_snapshot(
            value.model_copy(update=changes), "provider", "operation"
        ),
        "invalid_response",
    )


def forbidden(*args: object, **kwargs: object) -> Never:
    raise AssertionError("Unexpected side effect")


def test_guarded_uncached_import_construction_validation_and_real_energy_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code = compile(
        (ROOT / "phoenix/voice/vad/__init__.py").read_text(encoding="utf-8"),
        "vad.py",
        "exec",
    )
    incoming_frame = frame(960, amplitude=-32768)

    async def exercise() -> None:
        with monkeypatch.context() as scoped:
            for target, name in (
                (builtins, "open"),
                (Path, "open"),
                (Path, "read_text"),
                (Path, "write_text"),
                (os, "open"),
                (os, "mkdir"),
                (sqlite3, "connect"),
                (socket.socket, "__init__"),
                (socket, "getaddrinfo"),
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
            scoped.setattr(asyncio.get_running_loop(), "create_task", forbidden)
            if hasattr(ctypes, "WinDLL"):
                scoped.setattr(ctypes, "WinDLL", forbidden)

            def environment(mapping: object, key: str) -> str:
                if key == "PYDANTIC_DISABLE_PLUGINS":
                    raise KeyError(key)
                return forbidden()

            def getenv(key: str, default: object = None) -> object:
                return default if key == "PYDANTIC_DISABLE_PLUGINS" else forbidden()

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
            spec = importlib.util.spec_from_loader("isolated_vad", loader=None)
            assert spec is not None
            module = importlib.util.module_from_spec(spec)
            scoped.setitem(sys.modules, "isolated_vad", module)
            exec(code, module.__dict__)
            provider = module.EnergyVadProvider()
            incoming = module.VadRequest(frame=incoming_frame, duration="ms_30")
            module.VadRequest.model_validate_json(incoming.model_dump_json())
            (await provider.capabilities()).validate_provider(provider.provider_id)
            result = await provider.classify(incoming)
            result.validate_request(incoming, provider.provider_id)
            assert result.activity is module.VadActivity.SPEECH and result.score == 1.0
            assert (
                await provider.cancel(incoming.operation_id)
            ).status is CancellationStatus.UNSUPPORTED
            await provider.aclose()
            await provider.aclose()
            assert provider.closed and provider.active_operation_ids == ()

    run(exercise())


def test_fresh_interpreter_guarded_energy_operations() -> None:
    script = r"""
import asyncio, base64, builtins, ctypes, logging, os, socket, sqlite3
import subprocess, sys, threading, types
from pathlib import Path
from datetime import UTC, datetime
from hashlib import sha256
import pydantic
from phoenix.voice.audio import PcmAudioFrame
from phoenix.models.provider import CancellationStatus
source = Path("phoenix/voice/vad/__init__.py").read_text(encoding="utf-8")
code = compile(source, "vad.py", "exec")
loop = asyncio.new_event_loop()
def forbidden(*args, **kwargs):
    raise AssertionError("Unexpected external side effect")
async def exercise():
    for target, name in ((builtins,"open"),(builtins,"print"),(Path,"open"),
        (Path,"read_text"),(Path,"write_text"),(os,"open"),(os,"mkdir"),
        (socket.socket,"__init__"),(socket,"getaddrinfo"),(sqlite3,"connect"),
        (subprocess,"Popen"),(threading.Thread,"start"),(logging.Logger,"_log"),
        (asyncio,"create_task"),(asyncio,"ensure_future")):
        setattr(target, name, forbidden)
    loop.create_task = forbidden
    if hasattr(ctypes, "WinDLL"):
        ctypes.WinDLL = forbidden
    def environment(mapping, key):
        if key == "PYDANTIC_DISABLE_PLUGINS":
            raise KeyError(key)
        return forbidden()
    type(os.environ).__getitem__ = environment
    def getenv(key, default=None):
        return default if key == "PYDANTIC_DISABLE_PLUGINS" else forbidden()
    os.getenv = getenv
    importer = builtins.__import__
    def guarded_import(name, *args, **kwargs):
        if name.startswith("phoenix.") and name not in (
            "phoenix.voice.audio", "phoenix.models.provider"):
            return forbidden()
        return importer(name, *args, **kwargs)
    builtins.__import__ = guarded_import
    module = types.ModuleType("fresh_vad")
    sys.modules["fresh_vad"] = module
    namespace = module.__dict__
    exec(code, namespace)
    provider = namespace["EnergyVadProvider"]()
    assert provider._loop is None
    pcm = b"\0\0" * 160
    frame = PcmAudioFrame(stream_id="stream", sequence=0,
        captured_at=datetime(2026,10,7,tzinfo=UTC), sample_rate_hz=16000,
        channels=1, sample_width_bytes=2, encoding="pcm_s16le",
        pcm_base64=base64.b64encode(pcm).decode(), pcm_sha256=sha256(pcm).hexdigest())
    request = namespace["VadRequest"](frame=frame, duration="ms_10")
    (await provider.capabilities()).validate_provider(provider.provider_id)
    result = await provider.classify(request)
    result.validate_request(request, provider.provider_id)
    assert result.activity.value == "silence" and result.score == 0.0
    cancelled = await provider.cancel(request.operation_id)
    assert cancelled.status is CancellationStatus.UNSUPPORTED
    await provider.aclose()
    await provider.aclose()
    assert provider.closed and provider.active_operation_ids == ()
task = loop.create_task(exercise())
try:
    loop.run_until_complete(task)
finally:
    loop.close()
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_static_only_bounded_cpu_and_neutral_imports() -> None:
    tree = ast.parse(
        (ROOT / "phoenix/voice/vad/__init__.py").read_text(encoding="utf-8")
    )
    allowed = {
        "__future__",
        "abc",
        "asyncio",
        "math",
        "struct",
        "collections.abc",
        "datetime",
        "enum",
        "typing",
        "uuid",
        "pydantic",
        "phoenix.voice.audio",
        "phoenix.models.provider",
    }
    banned = {
        "open",
        "print",
        "exec",
        "eval",
        "create_task",
        "ensure_future",
        "to_thread",
        "run_in_executor",
        "publish",
        "subscribe",
        "execute",
        "getenv",
        "WinDLL",
        "Popen",
        "socket",
        "connect",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name in allowed for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module in allowed
            if node.module == "phoenix.models.provider":
                assert {alias.name for alias in node.names} == {
                    "CancellationResult",
                    "CancellationStatus",
                }
        elif isinstance(node, ast.Call):
            name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else ""
            )
            assert name not in banned
    assert "not semantic" in (vad.__doc__ or "") and "PHX-064" in (vad.__doc__ or "")
    assert not any(isinstance(value, EnergyVadProvider) for value in vars(vad).values())
    assert "480" in (EnergyVadProvider.__doc__ or "")


def test_protected_production_migrations_dependencies_and_events() -> None:
    assert len(EventType) == 32
    assert ScriptDirectory.from_config(
        Config(str(ROOT / "alembic.ini"))
    ).get_heads() == ["0007_app_settings"]


def test_repository_credential_signature_scan() -> None:
    paths = subprocess.check_output(
        ["git", "ls-files"], cwd=ROOT, text=True
    ).splitlines()
    paths.extend(["phoenix/voice/vad/__init__.py", "tests/test_vad.py"])
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
