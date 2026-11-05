"""The owner's wake phrase is "Phoenix, Rise"; "Rise, Phoenix" keeps working."""

import pytest

from phoenix.voice.engines import whisper as whisper_engine
from phoenix.voice.engines.pipeline import strip_wake_phrase
from phoenix.voice.engines.whisper import wake_phrase_score

THRESHOLD = whisper_engine.WAKE_DETECTION_THRESHOLD


@pytest.mark.parametrize(
    "heard",
    [
        "Phoenix, rise.",
        "Phoenix rise",
        "phoenix rise, what time is it?",
        "Okay, Phoenix, rise",
        "Phenix rice.",
        "Feenix rises",
        "Phoenix, arise",
        "Rise, Phoenix.",  # the old order still wakes
    ],
)
def test_both_orders_wake(heard: str) -> None:
    assert wake_phrase_score(heard) >= THRESHOLD


@pytest.mark.parametrize(
    "heard",
    ["Phoenix is a city in Arizona.", "Phoenix, open YouTube", "I love phoenix", "phoenix"],
)
def test_phoenix_alone_or_with_other_words_does_not_wake(heard: str) -> None:
    assert wake_phrase_score(heard) < THRESHOLD


@pytest.mark.parametrize(
    ("heard", "rest"),
    [
        ("Phoenix, rise. What time is it?", "What time is it?"),
        ("Hey Phoenix rise, open YouTube", "open YouTube"),
        ("Rise, Phoenix, design a site", "design a site"),
        ("Phoenix, rise", ""),
    ],
)
def test_the_wake_phrase_is_trimmed_from_the_request(heard: str, rest: str) -> None:
    assert strip_wake_phrase(heard).strip() == rest


# ---- "Stop, Phoenix" while PHOENIX is thinking

import asyncio  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from phoenix.voice.engines.pipeline import VoicePipeline, _TurnContext  # noqa: E402
from phoenix.voice.engines.whisper import stop_phrase_score  # noqa: E402


@pytest.mark.parametrize(
    "heard",
    ["Stop, Phoenix.", "stop phoenix", "Phoenix, stop", "Phoenix stop it", "Hey Phoenix stop", "Phenix, cancel"],
)
def test_stop_phrases_match(heard: str) -> None:
    assert stop_phrase_score(heard) >= THRESHOLD


@pytest.mark.parametrize(
    "heard",
    ["Phoenix is a city", "Please stop the music", "Phoenix, open YouTube", "I love phoenix", "Rise, Phoenix"],
)
def test_other_speech_is_not_a_stop(heard: str) -> None:
    assert stop_phrase_score(heard) < THRESHOLD


class _Frame:  # 30 ms of 16 kHz mono silence
    def pcm_bytes_for_provider(self) -> bytes:
        return b"\x00\x00" * 480


def _listener(speaking: list[bool], stop: bool):  # type: ignore[no-untyped-def]
    answers = iter(speaking)

    async def classify(_vad: object, _frame: object) -> bool:
        return next(answers)

    wake = SimpleNamespace(stop_heard=stop, calls=0)

    async def detect(_request: object) -> None:
        wake.calls += 1

    wake.detect = detect
    return SimpleNamespace(_classify=classify, _vad=None, _c=SimpleNamespace(wake=wake)), wake


def _run(speaking: list[bool], stop: bool) -> tuple[list[bool], int]:
    me, wake = _listener(speaking, stop)
    context = _TurnContext()

    async def go() -> list[bool]:
        return [await VoicePipeline._heard_stop(me, _Frame(), context) for _ in speaking]

    return asyncio.run(go()), wake.calls


def test_stop_listener_fires_only_when_the_utterance_ends_with_a_stop() -> None:
    answers, calls = _run([True] * 20 + [False] * 12, stop=True)
    assert answers[-1] is True and not any(answers[:-1]) and calls == 1


def test_stop_listener_ignores_other_speech_and_short_noises() -> None:
    assert _run([True] * 20 + [False] * 12, stop=False) == ([False] * 32, 1)
    short, calls = _run([True] * 3 + [False] * 12, stop=True)
    assert not any(short) and calls == 0  # a click is never even transcribed
