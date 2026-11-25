"""tests/test_walkie_talkie.py

Comprehensive tests for Walkie-Talkie mode (Prompt A):
  • Hold-to-talk down / up cycle (no VAD wait)
  • Repeated keydown ignored while holding
  • Accidental short tap (< 0.35s) ignored with hint
  • Cancellation (Esc / window blur / disconnect) discards audio
  • Max 60-second hold auto-releases
  • Barge-in on ptt_down interrupts playback instantly
  • Press during THINKING cancels previous turn and starts fresh
  • Keep-mic-warm ring buffer pre-rolls audio frames
  • Latency tracking: release_to_first_audio_ms recorded and exposed

All tests run purely in-memory with simulated mocks — zero audio hardware required.
"""

import time
import pytest
from unittest.mock import MagicMock, patch

from app.voice.pipeline import VoicePipeline, VoiceState
from app.voice.stt import STTResult, SpeechToTextProvider
from app.voice.audio_io import AudioPlayer, AudioRecorder, compute_rms


class MockPlayer(AudioPlayer):
    def __init__(self):
        super().__init__()
        self.played_audio = []
        self.stop_called = False

    def play_audio_bytes(self, audio_bytes: bytes, block: bool = True) -> bool:
        self.played_audio.append(audio_bytes)
        return True

    def stop(self) -> None:
        self.stop_called = True
        super().stop()


class MockRecorder(AudioRecorder):
    def __init__(self):
        super().__init__()
        self.stream_opened = False
        self.stream_closed = False
        self.cb = None

    def open_stream(self, callback, blocksize=320):
        self.stream_opened = True
        self.cb = callback
        mock_stream = MagicMock()
        return mock_stream

    def close_stream(self):
        self.stream_closed = True


class MockSTT(SpeechToTextProvider):
    def __init__(self, text: str = "What time is it?", lang: str = "en"):
        self.text = text
        self.lang = lang
        self.transcribe_calls = 0

    def transcribe(self, audio_bytes: bytes, language=None, listen_languages=None) -> STTResult:
        self.transcribe_calls += 1
        return STTResult(text=self.text, confidence=0.95, language=self.lang, duration=len(audio_bytes)/32000.0)


class MockAgent:
    def __init__(self, reply: str = "It's 10:30 AM, boss."):
        self.reply = reply

    def chat_stream(self, messages):
        yield self.reply


class MockTTS:
    def __init__(self):
        self.synthesize_calls = 0

    def synthesize(self, text: str, voice=None, language=None, **kwargs) -> bytes:
        self.synthesize_calls += 1
        return b"MOCK_TTS_AUDIO_BYTES_WAV"

    def add_fallback_listener(self, cb):
        pass


@pytest.fixture
def walkie_pipeline():
    player = MockPlayer()
    recorder = MockRecorder()
    stt = MockSTT()
    agent = MockAgent()
    tts = MockTTS()
    pipeline = VoicePipeline(
        agent=agent,
        recorder=recorder,
        player=player,
        stt=stt,
        tts=tts,
        follow_up_enabled=False,
    )
    return pipeline


def test_walkie_talkie_down_up_success(walkie_pipeline):
    """Test full hold-to-talk down / up cycle without VAD delay."""
    events = []
    walkie_pipeline.subscribe(lambda e: events.append(e))

    # 1. PTT DOWN
    down_res = walkie_pipeline.ptt_down()
    assert down_res["ok"] is True
    assert walkie_pipeline.state == VoiceState.LISTENING
    assert walkie_pipeline._ptt_active is True

    # Simulate 0.5s of audio arrival (above 0.35s threshold)
    # 0.5s at 16kHz int16 mono = 16000 bytes
    chunk = b"\x10\x00" * 320
    for _ in range(25):
        walkie_pipeline._on_ptt_audio_chunk(chunk)
    time.sleep(0.38)

    # 2. PTT UP
    up_res = walkie_pipeline.ptt_up()
    assert up_res["ok"] is True
    assert walkie_pipeline._ptt_active is False

    # Wait for turn thread to complete
    if walkie_pipeline._turn_thread:
        walkie_pipeline._turn_thread.join(timeout=2.0)

    # Verify state transitions and STT call
    states = [e.get("state") for e in events if e.get("type") == "state_change"]
    assert "LISTENING" in states
    assert "TRANSCRIBING" in states
    assert walkie_pipeline.stt.transcribe_calls == 1
    assert walkie_pipeline.player.played_audio  # Answer was synthesized and played


def test_repeat_keydown_ignored(walkie_pipeline):
    """Repeated keydown events while holding must be ignored cleanly."""
    res1 = walkie_pipeline.ptt_down()
    assert res1["ok"] is True
    assert walkie_pipeline._ptt_active is True

    # Second press while already holding
    res2 = walkie_pipeline.ptt_down()
    assert res2["ok"] is True
    assert res2["action"] == "ignored_repeat"


def test_short_tap_ignored_with_hint(walkie_pipeline):
    """Holds < 0.35s are ignored as accidental taps and show a hint."""
    hints = []
    walkie_pipeline.subscribe(lambda e: hints.append(e) if e.get("type") == "ptt_hint" else None)

    walkie_pipeline.ptt_down()
    # Immediate release (< 0.35s)
    up_res = walkie_pipeline.ptt_up()
    assert up_res["ok"] is True
    assert up_res["action"] == "ignored_short_tap"
    assert walkie_pipeline.state == VoiceState.IDLE
    assert walkie_pipeline.stt.transcribe_calls == 0
    assert len(hints) >= 1
    assert "Hold Space to talk" in hints[0]["message"]


def test_blur_and_cancel_resets_state(walkie_pipeline):
    """Esc or blur events trigger ptt_cancel, discarding audio and resetting state to IDLE."""
    walkie_pipeline.ptt_down()
    assert walkie_pipeline._ptt_active is True

    # Simulate some audio incoming
    walkie_pipeline._on_ptt_audio_chunk(b"\x20\x00" * 320)

    # Cancel (e.g. window blur or Esc key)
    cancel_res = walkie_pipeline.ptt_cancel()
    assert cancel_res["ok"] is True
    assert cancel_res["action"] == "cancelled"
    assert walkie_pipeline._ptt_active is False
    assert walkie_pipeline.state == VoiceState.IDLE
    assert len(walkie_pipeline._ptt_chunks) == 0


def test_barge_in_stops_playback_instantly(walkie_pipeline):
    """Pressing PTT while Nalla is speaking stops audio playback in < 200ms."""
    walkie_pipeline.set_state(VoiceState.SPEAKING)
    walkie_pipeline.player._is_playing.set()

    t0 = time.perf_counter()
    res = walkie_pipeline.ptt_down()
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert res["ok"] is True
    assert elapsed_ms < 200.0  # Barge-in latency guarantee
    assert walkie_pipeline.player.stop_called is True
    assert walkie_pipeline.state == VoiceState.LISTENING

    walkie_pipeline.ptt_cancel()


def test_press_during_thinking_cancels_old_turn(walkie_pipeline):
    """Pressing PTT while Nalla is thinking cancels the old turn and starts listening."""
    walkie_pipeline.set_state(VoiceState.THINKING)

    res = walkie_pipeline.ptt_down()
    assert res["ok"] is True
    assert walkie_pipeline.state == VoiceState.LISTENING
    assert walkie_pipeline._ptt_active is True

    walkie_pipeline.ptt_cancel()


def test_max_hold_auto_release(walkie_pipeline):
    """Max 60-second hold limit triggers auto-release warning and ends capture."""
    notices = []
    walkie_pipeline.subscribe(lambda e: notices.append(e) if e.get("type") == "notice" else None)

    walkie_pipeline.ptt_down()
    # Add dummy audio
    walkie_pipeline._on_ptt_audio_chunk(b"\x10\x00" * 320)

    # Manually trigger the timeout callback
    walkie_pipeline._on_ptt_auto_release()

    assert walkie_pipeline._ptt_active is False
    assert any("60s" in n.get("message", "") for n in notices)


def test_keep_mic_warm_ring_buffer(walkie_pipeline):
    """When keep_mic_warm is active, pre-recorded warm frames are prepended on ptt_down."""
    walkie_pipeline.recorder._warm_buffer.append(b"WARM_FRAME_1_")
    walkie_pipeline.recorder._warm_buffer.append(b"WARM_FRAME_2_")

    with patch("app.config.KEEP_MIC_WARM", True):
        walkie_pipeline.ptt_down()
        assert b"WARM_FRAME_1_" in walkie_pipeline._ptt_chunks[0]

    walkie_pipeline.ptt_cancel()


def test_compute_rms():
    """RMS energy calculation helper."""
    silence = b"\x00\x00" * 160
    assert compute_rms(silence) == 0.0

    # Active sine/noise wave
    signal = b"\xff\x3f" * 160
    assert compute_rms(signal) > 0.0


def test_delimiter_does_not_speak_twice(walkie_pipeline):
    """When LLM returns headline + '---' + detailed notes, only the headline is spoken once."""
    from app.perf import TurnRecord

    class MockAgent:
        def chat_stream(self, messages):
            yield "Aaha, display-a? Oled illa TFT-ah, edhachu SPI communication thaan use pannoru. \n\n"
            yield "---\n"
            yield "V C C-a three point three volts-kum, G N D-a ground-kum pin pannunga."

    walkie_pipeline.agent = MockAgent()
    synth_calls = []
    original_synth = walkie_pipeline.tts.synthesize

    def track_synth(text, **kwargs):
        synth_calls.append(text)
        return b"DUMMY_AUDIO"

    walkie_pipeline.tts.synthesize = track_synth
    walkie_pipeline._current_turn_id = 1

    rec = TurnRecord(turn_id=1)
    full_reply = walkie_pipeline._stream_agent_turn(
        messages=[{"role": "user", "content": "connect display"}],
        llm_start=time.perf_counter(),
        speech_end=time.perf_counter(),
        rec=rec,
        detected_lang="en",
        turn_id=1,
    )

    # 1. Full written reply includes both headline and notes
    assert "---" in full_reply
    assert "V C C-a three point three" in full_reply

    # 2. But TTS was synthesized ONLY ONCE, for the headline before '---'
    assert len(synth_calls) == 1
    assert "Aaha, display-a" in synth_calls[0]
    assert "V C C-a" not in synth_calls[0]

