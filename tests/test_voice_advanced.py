"""
tests/test_voice_advanced.py

Advanced Voice Pipeline Verification Suite:
  1. State Machine Transitions (IDLE -> LISTENING -> TRANSCRIBING -> THINKING -> SPEAKING -> IDLE)
  2. Barge-in Interruption Latency (< 200ms)
  3. Follow-up Window: 30-second post-reply listening without wake word
  4. Echo Control: Hardware-agnostic microphone zeroing / muting during SPEAKING
  5. WakeWord Interface & OpenWakeWord engine settings & fallback
  6. Language Routing: Tamil / English profile auto-selection
"""

import time
import pytest
from unittest.mock import MagicMock

from app.voice.audio_io import AudioPlayer, AudioRecorder
from app.voice.pipeline import VoicePipeline, VoiceState
from app.voice.stt import FasterWhisperSTT, STTResult
from app.voice.tts import TTSManager
from app.voice.wakeword import PushToTalk, OpenWakeWordDetector, WakeWordDetector


class MockAudioPlayer(AudioPlayer):
    def __init__(self):
        super().__init__()
        self.stopped = False
        self.played_chunks = []

    def play_audio_bytes(self, audio_bytes: bytes, block: bool = True) -> bool:
        self.played_chunks.append(audio_bytes)
        # Simulate small playback time unless stop is requested
        for _ in range(50):
            if self._stop_requested:
                break
            time.sleep(0.01)
        return True

    def stop(self) -> None:
        self.stopped = True
        super().stop()


class MockRecorder(AudioRecorder):
    def __init__(self):
        super().__init__()
        self.record_calls = 0

    def record_seconds(self, duration: float, sample_rate: int = 16000) -> bytes:
        self.record_calls += 1
        if self.is_muted:
            return b"\x00" * int(sample_rate * 2 * duration)
        # Simulated PCM audio
        return b"\x10\x00" * int(sample_rate * duration)


class MockSTT(FasterWhisperSTT):
    def __init__(self, text: str = "வணக்கம் பாஸ்", lang: str = "ta", confidence: float = 0.95):
        super().__init__()
        self.text = text
        self.lang = lang
        self.confidence = confidence

    def transcribe(self, audio_bytes: bytes, language: str | None = None) -> STTResult:
        return STTResult(text=self.text, confidence=self.confidence, language=self.lang)


class MockAgent:
    def __init__(self, reply: str = "Yes boss, sollunga!"):
        self.reply = reply

    def chat_stream(self, messages):
        yield self.reply


# ---------------------------------------------------------------------------
# 1. State Machine Transitions
# ---------------------------------------------------------------------------

def test_state_machine_cycle():
    state_events = []
    player = MockAudioPlayer()
    recorder = MockRecorder()
    stt = MockSTT(text="Hello Nalla", lang="en")
    agent = MockAgent(reply="Yes boss, how can I help you today?")

    pipeline = VoicePipeline(
        agent=agent,
        recorder=recorder,
        player=player,
        stt=stt,
        follow_up_enabled=False,
    )
    # Mock VAD to return 0.1s simulated audio instantly
    pipeline.vad.record_speech_segment = lambda **kw: b"\x10\x00" * 1600
    pipeline.subscribe(lambda evt: state_events.append(evt["state"]) if evt.get("type") == "state_change" else None)

    reply = pipeline.run_single_turn()
    assert reply is not None

    # Expected cycle: LISTENING -> TRANSCRIBING -> THINKING -> SPEAKING -> IDLE
    assert "LISTENING" in state_events
    assert "TRANSCRIBING" in state_events
    assert "THINKING" in state_events
    assert "SPEAKING" in state_events
    assert state_events[-1] == "IDLE"


# ---------------------------------------------------------------------------
# 2. Barge-in Interruption Latency (< 200 ms)
# ---------------------------------------------------------------------------

def test_barge_in_interruption_under_200ms():
    player = MockAudioPlayer()
    recorder = MockRecorder()
    pipeline = VoicePipeline(
        agent=MockAgent(),
        recorder=recorder,
        player=player,
        stt=MockSTT(),
        follow_up_enabled=False,
    )

    # Put pipeline in SPEAKING state
    pipeline.set_state(VoiceState.SPEAKING)
    assert pipeline.get_state() == VoiceState.SPEAKING
    assert recorder.is_muted is True  # Echo control active during SPEAKING

    # Keep VAD listening for speech
    pipeline.vad.record_speech_segment = lambda **kw: time.sleep(0.5) or b""

    # Trigger wake word / barge-in mid-speech
    t0 = time.perf_counter()
    pipeline.trigger_wakeword()
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    # MUST stop playback and start listening within 200 ms
    assert elapsed_ms < 200.0, f"Barge-in took {elapsed_ms:.1f} ms, expected < 200 ms"
    assert player.stopped is True
    assert pipeline.get_state() == VoiceState.LISTENING
    assert recorder.is_muted is False  # Unmuted for new user utterance


# ---------------------------------------------------------------------------
# 3. Follow-up Window (30s auto-listening countdown)
# ---------------------------------------------------------------------------

def test_follow_up_window_emits_timeout_and_closes():
    events = []
    player = MockAudioPlayer()
    recorder = MockRecorder()
    pipeline = VoicePipeline(
        agent=MockAgent(),
        recorder=recorder,
        player=player,
        stt=MockSTT(text="What time is it?"),
        follow_up_enabled=True,
        follow_up_timeout=2.0,  # Short timeout for test speed
    )

    call_count = 0

    def mock_record_speech(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Turn 1: user speaks
            return b"\x10\x00" * 1600
        else:
            # Turn 2: Follow-up silence (timeout)
            return b""

    pipeline.vad.record_speech_segment = mock_record_speech
    pipeline.subscribe(lambda evt: events.append(evt) if evt.get("type") == "state_change" else None)

    # Run turn with follow-up allowed
    pipeline.run_single_turn(allow_follow_up=True)

    # Check that follow-up event was emitted with timeout
    follow_up_events = [
        e for e in events
        if e.get("state") == "LISTENING" and e.get("follow_up") is True
    ]
    assert len(follow_up_events) >= 1
    assert follow_up_events[0]["timeout"] == 2.0
    # Final state should be IDLE after follow-up silence
    assert events[-1]["state"] == "IDLE"


# ---------------------------------------------------------------------------
# 4. Echo Control (Mic Muted while Speaking)
# ---------------------------------------------------------------------------

def test_echo_control_mic_muting():
    recorder = MockRecorder()
    pipeline = VoicePipeline(recorder=recorder)

    # Default IDLE -> unmuted
    assert recorder.is_muted is False

    # State -> SPEAKING -> must mute recorder
    pipeline.set_state(VoiceState.SPEAKING)
    assert recorder.is_muted is True

    # State -> LISTENING -> must unmute recorder
    pipeline.set_state(VoiceState.LISTENING)
    assert recorder.is_muted is False

    # State -> IDLE -> unmuted
    pipeline.set_state(VoiceState.IDLE)
    assert recorder.is_muted is False


# ---------------------------------------------------------------------------
# 5. WakeWord Interface & OpenWakeWord Engine
# ---------------------------------------------------------------------------

def test_wakeword_interface_and_settings():
    detector = OpenWakeWordDetector(
        keyword="hey_jarvis_v0.1.onnx",
        sensitivity=0.6,
    )
    assert detector.get_name() == "openWakeWord"
    assert detector.keyword == "hey_jarvis_v0.1.onnx"
    assert detector.sensitivity == 0.6

    # Test live settings update
    detector.update_settings("custom_nalla.onnx", 0.75)
    assert detector.keyword == "custom_nalla.onnx"
    assert detector.sensitivity == 0.75

    # PushToTalk fallback
    ptt = PushToTalk()
    assert ptt.get_name() == "Push-to-Talk"
    assert ptt.wait_for_detection(timeout=0.01) is False
    ptt.trigger()
    assert ptt.wait_for_detection(timeout=0.01) is True
    assert ptt.wait_for_detection(timeout=0.01) is False  # Resets after trigger


# ---------------------------------------------------------------------------
# 6. Language Routing: Tamil / English Profile Auto-Selection
# ---------------------------------------------------------------------------

def test_language_routing_tamil_detection():
    synthesized_calls = []

    class MockTTSManager(TTSManager):
        def synthesize(self, text, voice=None, language=None, **kwargs):
            synthesized_calls.append({"text": text, "voice": voice, "language": language})
            return b"WAV_MOCK"

    mock_tts = MockTTSManager()
    pipeline = VoicePipeline(
        agent=MockAgent(reply="நாளைக்கு மழை பெய்ய வாய்ப்பு இருக்கு"),
        tts=mock_tts,
        stt=MockSTT(text="மழை வருமா?", lang="ta"),
        follow_up_enabled=False,
    )
    pipeline.vad.record_speech_segment = lambda **kw: b"\x10\x00" * 1600

    pipeline.run_single_turn()

    assert len(synthesized_calls) >= 1
    # Language or text has Tamil character set detected
    call = synthesized_calls[0]
    assert call["language"] == "ta"
