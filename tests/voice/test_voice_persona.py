"""Agent D: acknowledgement, completion lines, site routing, timing, barge-in."""

from __future__ import annotations

import asyncio
import base64
import logging
import sys
import types
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import pytest

from phoenix.api.missions import MissionService
from phoenix.api.voice import MissionVoiceResponder, TurnTimer, instrument
from phoenix.models import prewarm
from phoenix.persistence.missions import MissionStatus
from phoenix.voice.ack import AckClips
from phoenix.voice.conversation import (
    AudioPlaybackRequest,
    AudioPlaybackStatus,
    VoiceResponseRequest,
)
from phoenix.voice.engines.sound import SoundDeviceAudioOutput
from phoenix.voice.persona import (
    VOICE_RULE,
    VOICE_RULE_ENV,
    apply_voice_rule,
    site_ready_line,
)
from phoenix.voice.tts import TtsAudio, TtsAudioEncoding


def audio(operation_id: str = "op", seconds: float = 0.1) -> TtsAudio:
    pcm = b"\x01\x00" * int(16000 * seconds)
    return TtsAudio(
        operation_id=operation_id,
        provider_id="fake",
        voice_profile_id="p",
        sample_rate_hz=16000,
        channels=1,
        sample_width_bytes=2,
        encoding=TtsAudioEncoding.PCM_S16LE,
        pcm_base64=base64.b64encode(pcm).decode("ascii"),
        pcm_sha256=sha256(pcm).hexdigest(),
    )


class FakeTts:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def synthesize(self, request: Any) -> TtsAudio:
        self.calls += 1
        if self.fail:
            raise RuntimeError("down")
        return audio(request.operation_id)


class FakeOutput:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.cancelled = False

    async def play(self, request: AudioPlaybackRequest) -> object:
        self.events.append("ack_start")
        try:
            await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        self.events.append("ack_end")
        return None


class FakeMissions:
    """Settles after `delay`; records cancel."""

    def __init__(self, events: list[str], delay: float = 0.1) -> None:
        self.events, self.delay = events, delay
        self.submitted: list[str] = []
        self.cancelled: list[str] = []
        self._t0 = 0.0

    async def create_conversation(self) -> str:
        return "c1"

    async def submit(self, conversation_id: str, text: str, path: object) -> str:
        self.submitted.append(text)
        self._t0 = asyncio.get_running_loop().time()
        return "m1"

    async def get_mission(self, mission_id: str) -> Any:
        done = asyncio.get_running_loop().time() - self._t0 >= self.delay
        if done:
            self.events.append("mission_done")
        return types.SimpleNamespace(
            status=MissionStatus.COMPLETED if done else MissionStatus.RUNNING,
            answer_message_id="a",
            conversation_id="c1",
        )

    async def list_messages(self, conversation_id: str) -> list[Any]:
        return [types.SimpleNamespace(message_id="a", text="Done.")]

    async def cancel(self, mission_id: str) -> None:
        self.cancelled.append(mission_id)


@dataclass
class Outcome:
    kind: str
    site_id: str | None = None
    message: str = ""


def request(text: str, turn: str = "t1") -> VoiceResponseRequest:
    return VoiceResponseRequest(session_id="s", turn_id=turn, transcript_text=text)


def responder(missions: FakeMissions, output: FakeOutput, **kw: Any):
    clips = AckClips(Path("unused"))
    clips._clips.append(audio())

    async def acknowledge(turn_id: str) -> bool:
        return await clips.play(cast(Any, output), "s", turn_id)

    return MissionVoiceResponder(
        cast(MissionService, missions), acknowledge=acknowledge, **kw
    )


def test_completion_line_from_real_counts_and_rule_off_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert site_ready_line(6) == "Boss, site ready. 6 sections."
    assert site_ready_line(1) == "Boss, site ready. 1 section."
    monkeypatch.delenv(VOICE_RULE_ENV, raising=False)
    assert apply_voice_rule("Q") == "Q"
    monkeypatch.setenv(VOICE_RULE_ENV, "1")
    assert apply_voice_rule("Q") == f"Q\n{VOICE_RULE}"


def test_ack_clips_render_once_then_load_from_disk(tmp_path: Path) -> None:
    first, second = FakeTts(), FakeTts(fail=True)
    cold, warm = AckClips(tmp_path), AckClips(tmp_path)
    asyncio.run(cold.prepare(cast(Any, first), "p"))
    asyncio.run(warm.prepare(cast(Any, second), "p"))
    assert cold.count == warm.count == first.calls == 3
    assert second.calls == 0  # all served from the cache


def test_ack_never_blocks_when_tts_is_unavailable(tmp_path: Path) -> None:
    clips = AckClips(tmp_path)
    asyncio.run(clips.prepare(cast(Any, FakeTts(fail=True)), "p"))
    assert clips.count == 0
    assert asyncio.run(clips.play(cast(Any, FakeOutput([])), "s", "t")) is False


def test_ack_plays_before_mission_finishes_and_before_answer() -> None:
    events: list[str] = []
    missions = FakeMissions(events)
    r = responder(missions, FakeOutput(events))
    draft = asyncio.run(r.respond(request("what is the weather")))
    assert events.index("ack_start") < events.index("mission_done")
    assert events[-1] != "ack_start" and "ack_end" in events  # output left free
    assert draft.response_text == "Done."


def test_site_outcome_speaks_templated_line_without_a_mission() -> None:
    events: list[str] = []
    missions = FakeMissions(events)
    seen: list[tuple[str, str | None]] = []

    async def command(text: str, active: str | None) -> Outcome:
        seen.append((text, active))
        return Outcome("site", "demo")

    polls = iter([{"status": "building"}, {"status": "ready", "sections": [1] * 6}])

    async def status(site_id: str) -> dict[str, object]:
        return next(polls)

    r = responder(
        missions, FakeOutput(events), site_command=command, site_status=status
    )
    draft = asyncio.run(r.respond(request("build me a portfolio site")))
    assert draft.response_text == "Boss, site ready. 6 sections."
    assert missions.submitted == [] and r.active_site_id == "demo"
    asyncio.run(r.respond(request("make it blue")))
    assert seen[-1] == ("make it blue", "demo")


def test_passthrough_and_failing_site_command_keep_the_mission_flow() -> None:
    for command in (_passthrough, _boom):
        missions = FakeMissions([])
        r = responder(missions, FakeOutput([]), site_command=command)
        assert asyncio.run(r.respond(request("hello"))).response_text == "Done."
        assert missions.submitted == ["hello"]


async def _passthrough(text: str, active: str | None) -> Outcome:
    return Outcome("passthrough")


async def _boom(text: str, active: str | None) -> Outcome:
    raise RuntimeError("x")


def test_cancel_flushes_ack_and_cancels_generation() -> None:
    events: list[str] = []
    missions, output = FakeMissions(events, delay=30), FakeOutput(events)
    r = responder(missions, output)

    async def go() -> None:
        task = asyncio.create_task(r.respond(request("long job")))
        await asyncio.sleep(0.3)  # ack finished, mission still running
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    assert missions.cancelled == ["m1"]

    events2: list[str] = []
    m2, o2 = FakeMissions(events2, delay=30), FakeOutput(events2)
    r2 = responder(m2, o2)

    async def early() -> None:
        task = asyncio.create_task(r2.respond(request("long job")))
        await asyncio.sleep(0.01)  # ack mid-playback
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.01)

    asyncio.run(early())
    assert o2.cancelled


def test_timing_logs_stages_with_ids_only(caplog: pytest.LogCaptureFixture) -> None:
    timer = TurnTimer()
    events: list[str] = []
    missions, output = FakeMissions(events), FakeOutput(events)

    async def transcribe(req: Any) -> str:
        return "secret transcript words"

    async def play(req: Any) -> None:
        return None

    comps = types.SimpleNamespace(
        stt=types.SimpleNamespace(transcribe=transcribe),
        output=types.SimpleNamespace(play=play),
    )
    instrument(cast(Any, comps), timer)
    r = responder(missions, output, timer=timer)

    async def go() -> None:
        await comps.stt.transcribe(types.SimpleNamespace(operation_id="t1"))
        await r.respond(request("secret transcript words"))
        await comps.output.play(types.SimpleNamespace(playback_id="t1"))

    with caplog.at_level(logging.INFO, logger="phoenix.voice.timing"):
        asyncio.run(go())
    text = " ".join(rec.getMessage() for rec in caplog.records)
    for stage in (
        "stage=stt",
        "stage=first_token",
        "stage=first_audio kind=ack",
        "stage=first_audio kind=answer",
    ):
        assert stage in text
    assert "secret" not in text


def test_barge_in_flushes_buffer_and_stops_playback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Stream:
        writes = 0
        aborted = False

        def __init__(self, **kw: Any) -> None:
            pass

        def __enter__(self) -> Stream:
            return self

        def __exit__(self, *a: object) -> None:
            return None

        def write(self, data: bytes) -> None:
            Stream.writes += 1
            import time

            time.sleep(0.01)

        def abort(self) -> None:
            Stream.aborted = True

    monkeypatch.setitem(
        sys.modules, "sounddevice", types.SimpleNamespace(RawOutputStream=Stream)
    )
    out = SoundDeviceAudioOutput()

    async def go() -> AudioPlaybackStatus:
        play = asyncio.create_task(
            out.play(
                AudioPlaybackRequest(
                    playback_id="p", session_id="s", turn_id="t", audio=audio(seconds=3)
                )
            )
        )
        await asyncio.sleep(0.1)
        await out.interrupt("p")
        return (await play).status

    assert asyncio.run(go()) is AudioPlaybackStatus.INTERRUPTED
    assert Stream.aborted and Stream.writes < 60  # 3 s = 60 blocks; stopped early


def test_prewarm_pins_model_30m_and_fails_soft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: dict[str, Any] = {}

    def post(url: str, **kw: Any) -> Any:
        sent.update(url=url, **kw["json"])
        return types.SimpleNamespace(raise_for_status=lambda: None)

    monkeypatch.setattr(prewarm.httpx, "post", post)
    assert prewarm.prewarm_ollama("http://x") is True
    assert sent["model"] == "qwen3.5:4b" and sent["keep_alive"] == "30m"

    def down(*a: Any, **k: Any) -> None:
        raise OSError

    monkeypatch.setattr(prewarm.httpx, "post", down)
    assert prewarm.prewarm_ollama("http://x") is False
    assert prewarm.start_prewarm() == []  # no threads under pytest


@pytest.fixture(autouse=True)
def _no_ack_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """The acknowledgement waits 1.5 s in real use; the fake missions finish at once."""
    import phoenix.api.voice as voice_module

    monkeypatch.setattr(voice_module, "ACK_DELAY_SECONDS", 0.0)
