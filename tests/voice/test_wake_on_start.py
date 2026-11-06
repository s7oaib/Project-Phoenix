"""The wake listener opens PHOENIX; the app then starts awake (no second "Phoenix")."""

import asyncio

import pytest

from phoenix.voice.engines.pipeline import VoicePipelineState
from tests.test_voice_pipeline import Rig, command


def test_started_by_the_listener_the_app_is_listening_without_a_wake_word(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHOENIX_WAKE_ON_START", "1")
    rig = Rig()
    rig.stt.texts.append("What is two plus two?")
    command(rig)  # a request with no wake phrase before it

    async def scenario() -> None:
        await rig.run_until(
            lambda: rig.pipeline.turn_count == 1 and rig.pipeline.wake_count == 0
        )

    asyncio.run(scenario())
    assert rig.pipeline.turn_count == 1 and rig.pipeline.wake_count == 0
    assert VoicePipelineState.LISTENING in rig.states


def test_without_the_signal_it_still_waits_for_the_wake_word(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PHOENIX_WAKE_ON_START", raising=False)
    rig = Rig()
    command(rig)  # speech but no wake word: must be ignored

    async def scenario() -> None:
        await asyncio.sleep(0.3)

    asyncio.run(scenario())
    assert rig.pipeline.turn_count == 0
