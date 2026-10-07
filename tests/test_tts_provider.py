"""PHX-063 synthetic PCM/text; no real voice engine, samples or audio device."""

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
from phoenix.security.permissions import PermissionGrant, UserPermissionDecision
from phoenix.tools.registry import ToolDefinition
from phoenix.voice import tts
from phoenix.voice.tts import (
    TtsAudio,
    TtsAudioEncoding,
    TtsCapabilities,
    TtsProvider,
    TtsProviderError,
    TtsProviderErrorCode,
    TtsRequest,
    TtsVoiceProfile,
    VoiceProfileProvenance,
)
from phoenix.voice.wake import DEFAULT_WAKE_PHRASE

ROOT = Path(__file__).resolve().parents[1]
PCM = b"\x01\x02\x03\x04"
TEXT = "  Synthetic original voice!\nΕλληνικά हिन्दी ಕನ್ನಡ 🌐\t  "
BAD = (True, 1, 1.5, b"text", object(), "\0", "\ud800")
BAD_IDS = BAD + (None, "", " ", "x\n", "\tx", "x\x7f", "x\x85")
T = TypeVar("T")


def run(value: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(value)


def profile(**changes: Any) -> TtsVoiceProfile:
    return TtsVoiceProfile.model_validate(
        dict(
            profile_id="Original-A",
            display_name="Synthetic original",
            provenance="original",
            languages=("en", "kn", "en-US"),
            is_primary=True,
        )
        | changes
    )


def capabilities(**changes: Any) -> TtsCapabilities:
    return TtsCapabilities.model_validate(
        dict(
            provider_id="Provider-A",
            voices=(profile(),),
            sample_rate_hz=16000,
            channels=1,
            sample_width_bytes=2,
            encoding="pcm_s16le",
            supports_language_hint=True,
            supports_cancellation=True,
        )
        | changes
    )


def request(**changes: Any) -> TtsRequest:
    return TtsRequest.model_validate(
        dict(
            operation_id="Operation-A",
            text=TEXT,
            voice_profile_id="Original-A",
            language_hint=None,
        )
        | changes
    )


def audio(data: bytes = PCM, /, **changes: Any) -> TtsAudio:
    return TtsAudio.model_validate(
        dict(
            operation_id="Operation-A",
            provider_id="Provider-A",
            voice_profile_id="Original-A",
            sample_rate_hz=16000,
            channels=1,
            sample_width_bytes=2,
            encoding="pcm_s16le",
            pcm_base64=base64.b64encode(data).decode(),
            pcm_sha256=sha256(data).hexdigest(),
        )
        | changes
    )


def fail(call: Callable[[], object], code: str = "invalid_response") -> None:
    with pytest.raises(TtsProviderError) as caught:
        call()
    assert str(caught.value) == code and caught.value.args == (code,)


def test_exact_vocabularies_and_public_boundary() -> None:
    assert [member.value for member in TtsAudioEncoding] == ["pcm_s16le"]
    assert [member.value for member in VoiceProfileProvenance] == [
        "original",
        "licensed",
        "consented",
    ]
    assert [member.value for member in TtsProviderErrorCode] == [
        "invalid_input",
        "invalid_response",
        "unsupported_voice",
        "unsupported_language",
        "unavailable",
        "timeout",
        "busy",
        "closed",
        "wrong_event_loop",
        "cleanup_failure",
    ]
    assert set(tts.__all__) == {
        "TtsAudioEncoding",
        "VoiceProfileProvenance",
        "TtsProviderErrorCode",
        "TtsProviderError",
        "TtsVoiceProfile",
        "TtsCapabilities",
        "TtsRequest",
        "TtsAudio",
        "TtsProvider",
    }
    assert "voice" in (tts.__doc__ or "") and "UTA" in (tts.__doc__ or "")
    assert "not independent rights/consent" in (tts.__doc__ or "")


@pytest.mark.parametrize("code", list(TtsProviderErrorCode))
def test_safe_error_strings(code: TtsProviderErrorCode) -> None:
    value = TtsProviderError(code)
    assert value.args == (code.value,) and str(value) == code.value


class Foreign(StrEnum):
    ERROR = "invalid_input"
    ENCODING = "pcm_s16le"
    ORIGINAL = "original"


def copied_enum(cls: type[StrEnum], member: StrEnum) -> StrEnum:
    copied = str.__new__(cls, member.value)
    copied._name_ = member.name
    copied._value_ = member.value
    return copied


def test_copied_foreign_or_raw_error_codes_rejected() -> None:
    for value in (
        copied_enum(TtsProviderErrorCode, TtsProviderErrorCode.INVALID_INPUT),
        Foreign.ERROR,
        "invalid_input",
        None,
        True,
        object(),
    ):
        with pytest.raises(ValueError, match="^invalid_input$"):
            TtsProviderError(value)  # type: ignore[arg-type]


FACTORIES: tuple[Callable[..., BaseModel], ...] = (
    profile,
    capabilities,
    request,
    audio,
)
FIELDS = (
    "profile_id display_name provenance languages is_primary",
    "provider_id voices sample_rate_hz channels sample_width_bytes encoding "
    "supports_language_hint supports_cancellation",
    "operation_id text voice_profile_id language_hint",
    "operation_id provider_id voice_profile_id sample_rate_hz channels "
    "sample_width_bytes encoding pcm_base64 pcm_sha256",
)


@pytest.mark.parametrize("factory,fields", zip(FACTORIES, FIELDS))
def test_exact_fields_frozen_detached_and_json(
    factory: Callable[..., BaseModel], fields: str
) -> None:
    value = factory()
    assert set(type(value).model_fields) == set(fields.split())
    assert type(value).model_validate_json(value.model_dump_json()) == value
    assert type(value).model_validate(value) is not value
    with pytest.raises(ValidationError):
        setattr(value, fields.split()[0], "changed")
    fail(
        lambda: type(value).model_validate(value.model_dump() | {"approved": True}),
        "invalid_input" if isinstance(value, TtsRequest) else "invalid_response",
    )


ID_FIELDS = [
    (profile, "profile_id", 128),
    (profile, "display_name", 256),
    (capabilities, "provider_id", 256),
    (request, "operation_id", 256),
    (request, "voice_profile_id", 128),
    (audio, "operation_id", 256),
    (audio, "provider_id", 256),
    (audio, "voice_profile_id", 128),
]


@pytest.mark.parametrize("factory,field,bound", ID_FIELDS)
@pytest.mark.parametrize("value", BAD_IDS)
def test_strict_identifiers(
    factory: Callable[..., BaseModel], field: str, bound: int, value: object
) -> None:
    fail(
        lambda: factory(**{field: value}),
        "invalid_input" if factory is request else "invalid_response",
    )


@pytest.mark.parametrize("factory,field,bound", ID_FIELDS)
def test_identifier_exact_normalization_and_bound(
    factory: Callable[..., BaseModel], field: str, bound: int
) -> None:
    assert (
        getattr(factory(**{field: "  Case-Ελληνικά 🌐  "}), field) == "Case-Ελληνικά 🌐"
    )
    assert getattr(factory(**{field: "x" * bound}), field) == "x" * bound
    fail(
        lambda: factory(**{field: "x" * (bound + 1)}),
        "invalid_input" if factory is request else "invalid_response",
    )


@pytest.mark.parametrize(
    "value", BAD + ("", " ", "x\n", "\tx", "x\x7f", "x\x85", "x" * 33)
)
def test_invalid_language_tokens(value: object) -> None:
    fail(lambda: request(language_hint=value), "invalid_input")
    fail(lambda: profile(languages=(value,)))


@pytest.mark.parametrize(
    "token", ["en", "en-US", "hi", "kn", " X-Custom ", "ಕನ್ನಡ", "x" * 32]
)
def test_opaque_language_tokens(token: str) -> None:
    assert request(language_hint=token).language_hint == token.strip()
    assert profile(languages=(token,)).languages == (token.strip(),)
    assert request().language_hint is None


@pytest.mark.parametrize(
    "value",
    [
        (),
        [],
        None,
        {},
        "en",
        {"en"},
        (None,),
        ("en", " en "),
        tuple(f"L{i}" for i in range(33)),
    ],
)
def test_invalid_language_containers_and_duplicates(value: object) -> None:
    fail(lambda: profile(languages=value))


def test_language_count_order_detach_and_case_sensitive_uniqueness() -> None:
    supplied = [f"L{i}" for i in range(32)]
    value = profile(languages=supplied)
    supplied.clear()
    assert value.languages == tuple(f"L{i}" for i in range(32))
    assert profile(languages=("en", "EN")).languages == ("en", "EN")


@pytest.mark.parametrize(
    "value",
    [
        "unknown",
        "Original",
        " licensed ",
        None,
        True,
        Foreign.ORIGINAL,
        copied_enum(VoiceProfileProvenance, VoiceProfileProvenance.ORIGINAL),
    ],
)
def test_provenance_exact(value: object) -> None:
    fail(lambda: profile(provenance=value))


@pytest.mark.parametrize(
    "factory,field",
    [
        (profile, "is_primary"),
        (capabilities, "supports_language_hint"),
        (capabilities, "supports_cancellation"),
    ],
)
@pytest.mark.parametrize("value", [0, 1, "true", "false", None, 0.0])
def test_strict_boolean_flags(
    factory: Callable[..., BaseModel], field: str, value: object
) -> None:
    fail(lambda: factory(**{field: value}))


@pytest.mark.parametrize("count", [1, 64])
def test_voice_count_detach_and_order(count: int) -> None:
    supplied = [
        profile(profile_id=f"Voice-{i}", is_primary=i == 0) for i in range(count)
    ]
    value = capabilities(voices=supplied)
    assert value.voices[0] is not supplied[0]
    supplied.clear()
    assert tuple(v.profile_id for v in value.voices) == tuple(
        f"Voice-{i}" for i in range(count)
    )


@pytest.mark.parametrize(
    "value",
    [
        (),
        [],
        None,
        {},
        "voice",
        {1},
        tuple(profile(profile_id=f"V{i}", is_primary=i == 0) for i in range(65)),
    ],
)
def test_invalid_voice_containers(value: object) -> None:
    fail(lambda: capabilities(voices=value))


@pytest.mark.parametrize(
    "voices",
    [
        (profile(is_primary=False),),
        (profile(), profile(profile_id="Other")),
        (profile(), profile(is_primary=False)),
        (profile(), profile(profile_id=" Original-A ", is_primary=False)),
        (profile(provenance="licensed"),),
        (profile(provenance="consented"),),
    ],
)
def test_primary_count_original_and_unique_profile_policy(
    voices: tuple[TtsVoiceProfile, ...],
) -> None:
    fail(lambda: capabilities(voices=voices))


@pytest.mark.parametrize("provenance", list(VoiceProfileProvenance))
def test_optional_profiles_declared_provenance_is_inert(
    provenance: VoiceProfileProvenance,
) -> None:
    secondary = profile(profile_id="Optional", provenance=provenance, is_primary=False)
    value = capabilities(voices=(profile(), secondary))
    assert value.voices[1].provenance is provenance
    assert set(TtsVoiceProfile.model_fields) == set(FIELDS[0].split())
    request(voice_profile_id="Optional").validate_capabilities(value)


@pytest.mark.parametrize("factory", [capabilities, audio])
@pytest.mark.parametrize("value", [8000, 16000, 22050, 24000, 44100, 48000])
def test_sample_rate_admitted(factory: Callable[..., BaseModel], value: int) -> None:
    assert getattr(factory(sample_rate_hz=value), "sample_rate_hz") == value


@pytest.mark.parametrize("factory", [capabilities, audio])
@pytest.mark.parametrize("value", [7999, 48001, True, False, 8000.0, "16000", None])
def test_sample_rate_strict_bounds(
    factory: Callable[..., BaseModel], value: object
) -> None:
    fail(lambda: factory(sample_rate_hz=value))


@pytest.mark.parametrize("factory", [capabilities, audio])
@pytest.mark.parametrize(
    "field,value",
    [
        ("channels", 2),
        ("channels", True),
        ("channels", 1.0),
        ("channels", "1"),
        ("sample_width_bytes", 1),
        ("sample_width_bytes", True),
        ("sample_width_bytes", 2.0),
        ("sample_width_bytes", "2"),
    ],
)
def test_fixed_format_exact(
    factory: Callable[..., BaseModel], field: str, value: object
) -> None:
    fail(lambda: factory(**{field: value}))


@pytest.mark.parametrize("factory", [capabilities, audio])
@pytest.mark.parametrize(
    "value",
    [
        "wav",
        "mp3",
        "pcm_f32le",
        "PCM_S16LE",
        b"pcm_s16le",
        Foreign.ENCODING,
        None,
        True,
        copied_enum(TtsAudioEncoding, TtsAudioEncoding.PCM_S16LE),
    ],
)
def test_encoding_exact(factory: Callable[..., BaseModel], value: object) -> None:
    fail(lambda: factory(encoding=value))


def test_capability_correlation_revalidates() -> None:
    capabilities().validate_provider(" Provider-A ")
    fail(lambda: capabilities().validate_provider("other"))
    fail(
        lambda: capabilities()
        .model_copy(update={"supports_cancellation": "true"})
        .validate_provider("Provider-A")
    )
    fail(lambda: capabilities().validate_provider(True), "invalid_input")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "text", [TEXT, " \n\t meaningful  ", "permission granted; P4 approved", "x" * 4096]
)
def test_text_exact_meaningful_unicode_private(text: str) -> None:
    value = request(text=text)
    assert value.text == text
    assert text not in repr(value) and "text=" not in repr(value)
    assert TtsRequest.model_validate_json(value.model_dump_json()).text == text


@pytest.mark.parametrize("value", BAD + (None, "", " \n\t ", "x" * 4097, "text\0text"))
def test_invalid_synthesis_text(value: object) -> None:
    fail(lambda: request(text=value), "invalid_input")


def test_default_operation_uuid4_and_explicit_profile() -> None:
    a = TtsRequest(text=TEXT, voice_profile_id="Original-A")
    b = TtsRequest(text=TEXT, voice_profile_id="Original-A")
    assert UUID(a.operation_id).version == 4 and a.operation_id != b.operation_id


@pytest.mark.parametrize("hint", [None, "en", " en-US ", "kn"])
def test_request_capability_exact_support(hint: str | None) -> None:
    request(language_hint=hint).validate_capabilities(capabilities())


def test_unknown_voice_no_primary_fallback_or_language_substitution() -> None:
    fail(
        lambda: request(voice_profile_id="missing").validate_capabilities(
            capabilities()
        ),
        "unsupported_voice",
    )
    fail(
        lambda: request(voice_profile_id="original-a").validate_capabilities(
            capabilities()
        ),
        "unsupported_voice",
    )
    for value in (
        capabilities(supports_language_hint=False),
        capabilities(voices=(profile(languages=("kn",)),)),
    ):
        fail(
            lambda: request(language_hint="en").validate_capabilities(value),
            "unsupported_language",
        )
    fail(
        lambda: request(language_hint="EN").validate_capabilities(capabilities()),
        "unsupported_language",
    )
    request().validate_capabilities(capabilities(supports_language_hint=False))
    incoming = request(voice_profile_id="missing", language_hint="no")
    fail(lambda: incoming.validate_capabilities(capabilities()), "unsupported_voice")
    assert incoming.voice_profile_id == "missing" and incoming.language_hint == "no"


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
def test_exact_stored_attacks(factory: Callable[..., BaseModel], attack: str) -> None:
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
    fail(
        lambda: cls.model_validate(value),
        "invalid_input" if cls is TtsRequest else "invalid_response",
    )


def test_nested_profile_and_consumer_revalidation() -> None:
    bad = profile().model_copy(update={"is_primary": "true"})
    fail(lambda: capabilities(voices=(bad,)))
    fail(
        lambda: request().validate_capabilities(
            capabilities().model_copy(update={"voices": (bad,)})
        )
    )
    bad_request = request().model_copy(update={"text": " "})
    fail(lambda: bad_request.validate_capabilities(capabilities()), "invalid_input")
    fail(lambda: request().validate_capabilities(None))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value",
    [
        None,
        b"AQIDBA==",
        "",
        "not-base64",
        "AQIDBA==\n",
        " AQIDBA==",
        "AQIDBA== ",
        "AQID\nBA==",
        "AQIDBA",
        "AQIDBB==",
        "AQIDBA===",
        "-_==",
        "é==",
        "AA==",
        "AQID",
    ],
)
def test_pcm_canonical_nonempty_even(value: object) -> None:
    fail(lambda: audio(pcm_base64=value))


@pytest.mark.parametrize(
    "value",
    [
        None,
        b"hash",
        "",
        "A" * 64,
        "a" * 63,
        "a" * 65,
        "g" * 64,
        "a" * 64,
        sha256(PCM).hexdigest().upper(),
    ],
)
def test_hash_exact_lowercase_and_correlated(value: object) -> None:
    fail(lambda: audio(pcm_sha256=value))


def test_pcm_byte_and_duration_bounds() -> None:
    data = b"a" * 8388608
    admitted = audio(data, sample_rate_hz=48000)
    assert admitted.pcm_bytes_for_playback() == data
    assert admitted.duration_seconds == len(data) / 96000.0
    fail(lambda: audio(data + b"aa", sample_rate_hz=48000))
    exact = audio(b"a" * 1920000, sample_rate_hz=8000)
    assert exact.duration_seconds == 120.0
    fail(lambda: audio(b"a" * 1920002, sample_rate_hz=8000))


def test_pcm_pure_exact_uncached_and_private() -> None:
    value = audio()
    before = vars(value).copy()
    for _ in range(3):
        assert value.pcm_bytes_for_playback() == PCM
        assert value.duration_seconds == 4 / 32000.0
    assert vars(value) == before and set(vars(value)) == set(FIELDS[3].split())
    assert "pcm_base64" not in repr(value) and value.pcm_base64 not in repr(value)
    assert not value.pcm_bytes_for_playback().startswith(b"RIFF")
    assert audio(b"a" * 32000).duration_seconds == 1.0


@pytest.mark.parametrize(
    "changes",
    [
        {"pcm_sha256": "a" * 64},
        {"sample_rate_hz": True},
        {"pcm_base64": "AA=="},
        {"approved": True},
        {"encoding": Foreign.ENCODING},
    ],
)
def test_audio_helpers_exact_revalidation(changes: dict[str, object]) -> None:
    bad = audio().model_copy(update=changes)
    fail(bad.pcm_bytes_for_playback)
    fail(lambda: bad.duration_seconds)
    fail(lambda: bad.validate_request(request(), capabilities()))


@pytest.mark.parametrize(
    "changes",
    [
        {"operation_id": "other"},
        {"provider_id": "other"},
        {"voice_profile_id": "Other"},
        {"sample_rate_hz": 22050},
        {"channels": 2},
        {"sample_width_bytes": 1},
        {"encoding": "wav"},
    ],
)
def test_all_seven_audio_correlations(changes: dict[str, object]) -> None:
    value = audio().model_copy(update=changes)
    fail(lambda: value.validate_request(request(), capabilities()))


def test_audio_correlation_request_validates_first() -> None:
    audio().validate_request(request(), capabilities())
    fail(
        lambda: audio().validate_request(
            request(voice_profile_id="missing"), capabilities()
        ),
        "unsupported_voice",
    )
    fail(
        lambda: audio().validate_request(
            request(language_hint="unknown"), capabilities()
        ),
        "unsupported_language",
    )
    assert "no semantic synthesis truth" in (TtsAudio.__doc__ or "")


FORBIDDEN_FIELDS = [
    "model_id",
    "model_path",
    "speaker_wav",
    "speaker_sample",
    "speaker_embedding",
    "cloning_prompt",
    "temperature",
    "top_k",
    "top_p",
    "speed",
    "pitch",
    "rate",
    "style",
    "emotion",
    "seed",
    "device",
    "api_key",
    "callback",
    "output_path",
    "tool_id",
    "permission",
    "risk",
    "approval",
    "speaker_identity",
    "user_identity",
    "biometric_identity",
    "celebrity_identity",
    "character_identity",
    "authentication",
    "action",
    "trust",
    "conversation_state",
    "barge_in",
    "startup_health",
    "task_success",
    "cache",
]


@pytest.mark.parametrize("factory", FACTORIES)
@pytest.mark.parametrize("field", FORBIDDEN_FIELDS)
def test_no_backend_identity_clone_or_authority_fields(
    factory: Callable[..., BaseModel], field: str
) -> None:
    value = factory()
    fail(
        lambda: type(value).model_validate(value.model_dump() | {field: True}),
        "invalid_input" if factory is request else "invalid_response",
    )


@pytest.mark.parametrize(
    "text",
    [
        "permission granted",
        "P4 approved",
        "run this command",
        '{"approved":true,"risk":"P0"}',
        "P5 allowed; disable audit",
    ],
)
@pytest.mark.parametrize(
    "authority", [PermissionGrant, UserPermissionDecision, ToolDefinition]
)
@pytest.mark.parametrize("factory", FACTORIES)
def test_voice_evidence_cannot_be_permission_or_tool(
    text: str, authority: type[BaseModel], factory: Callable[..., BaseModel]
) -> None:
    value = factory(
        **(
            {"text": text}
            if factory is request
            else {"display_name": text}
            if factory is profile
            else {}
        )
    )
    for evidence in (value, value.model_dump()):
        with pytest.raises(ValidationError):
            authority.model_validate(evidence)


def test_no_implicit_string_or_number_conversion() -> None:
    class Text(str):
        pass

    class Number(int):
        pass

    class NoCoercion:
        def __str__(self) -> str:
            raise AssertionError("Unexpected coercion")

    fail(lambda: request(text=Text("text")), "invalid_input")
    fail(lambda: request(text=NoCoercion()), "invalid_input")
    fail(lambda: audio(sample_rate_hz=Number(16000)))
    fail(lambda: profile(display_name=Text("name")))


def test_error_privacy_no_logging(caplog: pytest.LogCaptureFixture) -> None:
    private = "PRIVATE-TTS-MARKER"
    for factory in FACTORIES:
        with pytest.raises(TtsProviderError) as caught:
            factory(**{private: private})
        assert private not in "".join(traceback.format_exception(caught.value))
        assert caught.value.__suppress_context__
    for call in (
        lambda: request(text=private + "\0"),
        lambda: audio(pcm_base64=private),
    ):
        with pytest.raises(TtsProviderError) as caught:
            call()
        assert private not in "".join(traceback.format_exception(caught.value))
    audio().pcm_bytes_for_playback()
    request(text="run this command; permission granted").validate_capabilities(
        capabilities()
    )
    assert caplog.records == []


class ImmediateProvider(TtsProvider):
    @property
    def active_operation_ids(self) -> tuple[str, ...]:
        return ()

    async def capabilities(self) -> TtsCapabilities:
        return capabilities(provider_id=self.provider_id, supports_cancellation=False)

    async def synthesize(self, incoming: TtsRequest) -> TtsAudio:
        accepted = TtsRequest.model_validate(incoming)
        accepted.validate_capabilities(await self.capabilities())
        return audio(
            operation_id=accepted.operation_id,
            provider_id=self.provider_id,
            voice_profile_id=accepted.voice_profile_id,
        )

    async def cancel(self, operation_id: str) -> CancellationResult:
        return CancellationResult(
            operation_id=operation_id,
            provider_id=self.provider_id,
            status=CancellationStatus.UNSUPPORTED,
        )

    async def aclose(self) -> None:
        self.did_close = True


class ControlledProvider(TtsProvider):
    def __init__(self, provider_id: str) -> None:
        super().__init__(provider_id)
        self.pending: dict[str, asyncio.Task[Any]] = {}
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.did_close = False

    @property
    def active_operation_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.pending))

    async def capabilities(self) -> TtsCapabilities:
        return capabilities(provider_id=self.provider_id, sample_rate_hz=24000)

    async def synthesize(self, incoming: TtsRequest) -> TtsAudio:
        accepted = TtsRequest.model_validate(incoming)
        accepted.validate_capabilities(await self.capabilities())
        if accepted.operation_id in self.pending:
            raise TtsProviderError(TtsProviderErrorCode.BUSY)
        task = asyncio.current_task()
        assert task is not None
        self.pending[accepted.operation_id] = task
        self.entered.set()
        try:
            await self.release.wait()
            return audio(
                b"\x05\x06",
                operation_id=accepted.operation_id,
                provider_id=self.provider_id,
                voice_profile_id=accepted.voice_profile_id,
                sample_rate_hz=24000,
            )
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
        tasks = tuple(self.pending.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.did_close = True


async def consume(provider: TtsProvider, incoming: TtsRequest) -> TtsAudio:
    info = TtsCapabilities.model_validate(await provider.capabilities())
    info.validate_provider(provider.provider_id)
    accepted = TtsRequest.model_validate(incoming)
    accepted.validate_capabilities(info)
    value = TtsAudio.model_validate(await provider.synthesize(accepted))
    value.validate_request(accepted, info)
    return value


def cancel_snapshot(
    value: object, provider_id: str, operation_id: str
) -> CancellationResult:
    if type(value) is not CancellationResult or value.__pydantic_extra__:
        raise TtsProviderError(TtsProviderErrorCode.INVALID_RESPONSE)
    try:
        result = CancellationResult.model_validate(dict(vars(value)))
    except ValidationError:
        raise TtsProviderError(TtsProviderErrorCode.INVALID_RESPONSE) from None
    if (result.provider_id, result.operation_id) != (provider_id, operation_id):
        raise TtsProviderError(TtsProviderErrorCode.INVALID_RESPONSE)
    return result


def test_complete_and_incomplete_abc_readonly_identity() -> None:
    members = {"active_operation_ids", "capabilities", "synthesize", "cancel", "aclose"}
    assert TtsProvider.__abstractmethods__ == members
    with pytest.raises(TypeError):
        TtsProvider("abstract")  # type: ignore[abstract]
    for missing in members:
        incomplete = type(
            "Incomplete",
            (TtsProvider,),
            {key: ImmediateProvider.__dict__[key] for key in members if key != missing},
        )
        with pytest.raises(TypeError):
            incomplete("incomplete")
        if missing != "active_operation_ids":
            assert inspect.iscoroutinefunction(getattr(TtsProvider, missing))
    provider = ImmediateProvider(" Provider-Case ")
    assert provider.provider_id == "Provider-Case"
    with pytest.raises(AttributeError):
        provider.provider_id = "other"  # type: ignore[misc]
    with pytest.raises(TypeError):
        ImmediateProvider("id", model_path="path")  # type: ignore[call-arg]


@pytest.mark.parametrize("value", BAD_IDS + ("x" * 257,))
def test_provider_identity_strict(value: object) -> None:
    fail(lambda: ImmediateProvider(value), "invalid_input")  # type: ignore[arg-type]


def test_two_independent_providers_substitutable() -> None:
    async def exercise() -> None:
        a = ImmediateProvider("A")
        b = ControlledProvider("B")
        b.release.set()
        incoming = request(language_hint="en")
        before = incoming.model_dump_json()
        for provider in (a, b):
            result = await consume(provider, incoming)
            assert result.provider_id == provider.provider_id
            assert result.operation_id == incoming.operation_id
            assert (
                provider.active_operation_ids == ()
                and incoming.model_dump_json() == before
            )
            cancel_snapshot(
                await provider.cancel("absent"), provider.provider_id, "absent"
            )
            await provider.aclose()
            assert provider.did_close
        assert (await consume(a, incoming)).pcm_bytes_for_playback() == PCM
        assert (await consume(b, incoming)).pcm_bytes_for_playback() == b"\x05\x06"

    run(exercise())


def test_scoped_cancellation_and_cleanup() -> None:
    async def exercise() -> None:
        provider = ControlledProvider("Provider-B")
        a = asyncio.create_task(consume(provider, request(operation_id="Z-A")))
        await asyncio.wait_for(provider.entered.wait(), 1)
        provider.entered.clear()
        b = asyncio.create_task(consume(provider, request(operation_id="A-B")))
        await asyncio.wait_for(provider.entered.wait(), 1)
        assert provider.active_operation_ids == ("A-B", "Z-A")
        with pytest.raises(TtsProviderError) as caught:
            await provider.synthesize(request(operation_id="Z-A"))
        assert caught.value.code is TtsProviderErrorCode.BUSY
        result = cancel_snapshot(
            await provider.cancel("Z-A"), provider.provider_id, "Z-A"
        )
        assert result.status is CancellationStatus.ACCEPTED
        with pytest.raises(asyncio.CancelledError):
            await a
        assert provider.active_operation_ids == ("A-B",) and not b.done()
        assert (await provider.cancel("absent")).status is CancellationStatus.NOT_FOUND
        provider.release.set()
        assert (await asyncio.wait_for(b, 1)).operation_id == "A-B"
        assert provider.active_operation_ids == ()
        await provider.aclose()
        assert provider.did_close

    run(exercise())


def test_fake_explicit_close_cleans_pending_operation() -> None:
    async def exercise() -> None:
        provider = ControlledProvider("B")
        pending = asyncio.create_task(consume(provider, request()))
        await asyncio.wait_for(provider.entered.wait(), 1)
        await provider.aclose()
        assert provider.did_close and provider.active_operation_ids == ()
        assert pending.cancelled()

    run(exercise())


@pytest.mark.parametrize(
    "kind", ["cap-provider", "cap-forged", "audio-provider", "audio-forged"]
)
def test_consumer_exact_revalidation(kind: str) -> None:
    class Corrupt(ImmediateProvider):
        async def capabilities(self) -> TtsCapabilities:
            value = await super().capabilities()
            return (
                value.model_copy(update={"provider_id": "wrong"})
                if kind == "cap-provider"
                else (
                    value.model_copy(update={"supports_language_hint": "true"})
                    if kind == "cap-forged"
                    else value
                )
            )

        async def synthesize(self, incoming: TtsRequest) -> TtsAudio:
            value = await super().synthesize(incoming)
            return (
                value.model_copy(update={"provider_id": "wrong"})
                if kind == "audio-provider"
                else (
                    value.model_copy(update={"pcm_sha256": "a" * 64})
                    if kind == "audio-forged"
                    else value
                )
            )

    fail(lambda: run(consume(Corrupt("provider"), request())))


@pytest.mark.parametrize(
    "changes",
    [
        {"provider_id": "wrong"},
        {"operation_id": "wrong"},
        {"status": "wrong"},
        {"approved": True},
    ],
)
def test_cancellation_correlations(changes: dict[str, object]) -> None:
    value = CancellationResult(
        provider_id="provider",
        operation_id="operation",
        status=CancellationStatus.ACCEPTED,
    )
    fail(
        lambda: cancel_snapshot(
            value.model_copy(update=changes), "provider", "operation"
        )
    )


def forbidden(*args: object, **kwargs: object) -> Never:
    raise AssertionError("Unexpected external side effect")


def test_guarded_uncached_import_construction_validation_and_synthesis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code = compile(
        (ROOT / "phoenix/voice/tts/__init__.py").read_text(encoding="utf-8"),
        "tts.py",
        "exec",
    )
    data = audio().model_dump(mode="json")

    async def exercise() -> None:
        with monkeypatch.context() as scoped:
            for target, name in (
                (builtins, "open"),
                (builtins, "print"),
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
                (logging.Logger, "_log"),
                (asyncio, "create_task"),
                (asyncio, "ensure_future"),
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
            spec = importlib.util.spec_from_loader("isolated_tts", loader=None)
            assert spec is not None
            module = importlib.util.module_from_spec(spec)
            scoped.setitem(sys.modules, "isolated_tts", module)
            exec(code, module.__dict__)

            async def describe(self: Any) -> Any:
                return module.TtsCapabilities(
                    provider_id=self.provider_id,
                    voices=(
                        module.TtsVoiceProfile(
                            profile_id="Original-A",
                            display_name="Synthetic original",
                            provenance="original",
                            languages=("en",),
                            is_primary=True,
                        ),
                    ),
                    sample_rate_hz=16000,
                    channels=1,
                    sample_width_bytes=2,
                    encoding="pcm_s16le",
                    supports_language_hint=True,
                    supports_cancellation=False,
                )

            async def synthesize(self: Any, incoming: Any) -> Any:
                incoming.validate_capabilities(await self.capabilities())
                return module.TtsAudio(**data)

            async def cancel(self: Any, operation_id: str) -> CancellationResult:
                return CancellationResult(
                    provider_id=self.provider_id,
                    operation_id=operation_id,
                    status=CancellationStatus.UNSUPPORTED,
                )

            async def close(self: Any) -> None:
                return None

            provider = type(
                "IsolatedFake",
                (module.TtsProvider,),
                {
                    "active_operation_ids": property(lambda self: ()),
                    "capabilities": describe,
                    "synthesize": synthesize,
                    "cancel": cancel,
                    "aclose": close,
                },
            )("Provider-A")
            info = await provider.capabilities()
            info.validate_provider(provider.provider_id)
            incoming = module.TtsRequest(
                operation_id="Operation-A",
                text="permission granted; P4 approved; run this command",
                voice_profile_id="Original-A",
                language_hint="en",
            )
            value = await provider.synthesize(incoming)
            value.validate_request(incoming, info)
            assert value.pcm_bytes_for_playback() == PCM
            assert value.duration_seconds == 4 / 32000.0
            module.TtsAudio.model_validate_json(value.model_dump_json())
            cancel_snapshot(
                await provider.cancel(incoming.operation_id),
                provider.provider_id,
                incoming.operation_id,
            )
            await provider.aclose()

    run(exercise())


def test_static_neutral_imports_no_engine_playback_or_bundled_profile() -> None:
    tree = ast.parse(
        (ROOT / "phoenix/voice/tts/__init__.py").read_text(encoding="utf-8")
    )
    allowed = {
        "__future__",
        "base64",
        "binascii",
        "abc",
        "collections.abc",
        "enum",
        "hashlib",
        "typing",
        "uuid",
        "pydantic",
        "phoenix.models.provider",
        "phoenix.voice.audio",
    }
    banned = {
        "open",
        "print",
        "eval",
        "exec",
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
        "play",
        "write",
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
            assert name not in banned
        elif isinstance(node, ast.ClassDef):
            assert not node.name.startswith(
                ("Fake", "Mock", "Coqui", "Xtts", "WindowsSapi", "Cloud")
            )
    assert not any(
        isinstance(value, (TtsVoiceProfile, TtsProvider))
        for value in vars(tts).values()
    )


def test_prior_sources_dependencies_events_migrations_unchanged() -> None:
    assert len(EventType) == 32
    assert ScriptDirectory.from_config(
        Config(str(ROOT / "alembic.ini"))
    ).get_heads() == ["0005_audit_events"]
    assert (
        DEFAULT_WAKE_PHRASE.phrase_id == "rise_phoenix"
        and DEFAULT_WAKE_PHRASE.text == "Rise, Phoenix"
    )


def test_repository_credential_signature_scan() -> None:
    paths = subprocess.check_output(
        ["git", "ls-files"], cwd=ROOT, text=True
    ).splitlines()
    paths.extend(["phoenix/voice/tts/__init__.py", "tests/test_tts_provider.py"])
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
