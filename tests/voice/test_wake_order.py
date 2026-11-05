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
